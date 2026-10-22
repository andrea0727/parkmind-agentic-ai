"""LLM_PROVIDER picks the chat model behind the agents; the agents themselves do not change."""

import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from parkmind.agents import llm_client
from parkmind.agents.ask_agent import AnthropicQuestionWriter
from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.agents.explain_agent import AnthropicNarrator
from parkmind.config.settings import PROVIDERS, settings

SRC = Path(__file__).resolve().parents[2] / "src"


class _FakeChat:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def bind_tools(self, tools: list[Any], tool_choice: Any = None) -> "_FakeChat":
        self.bound = (tools, tool_choice)
        return self


def _fake_package(monkeypatch, module: str, cls: str) -> None:
    fake = types.ModuleType(module)
    setattr(fake, cls, type(cls, (_FakeChat,), {}))
    monkeypatch.setitem(sys.modules, module, fake)


@pytest.mark.parametrize(
    ("provider", "module", "cls", "key_arg", "max_arg"),
    [
        (
            "anthropic",
            "langchain_anthropic",
            "ChatAnthropic",
            "api_key",
            "max_tokens_to_sample",
        ),
        (
            "openai",
            "langchain_openai",
            "ChatOpenAI",
            "api_key",
            "max_completion_tokens",
        ),
        (
            "google",
            "langchain_google_genai",
            "ChatGoogleGenerativeAI",
            "google_api_key",
            "max_output_tokens",
        ),
    ],
)
def test_each_provider_builds_its_chat_model_with_no_client_retries(
    monkeypatch, provider, module, cls, key_arg, max_arg
):
    _fake_package(monkeypatch, module, cls)

    chat = llm_client.build_chat_model(
        model="m",
        api_key="secret",
        temperature=0.3,
        max_tokens=77,
        timeout=9,
        provider=provider,
    )

    assert type(chat).__name__ == cls
    assert chat.kwargs[max_arg] == 77
    assert chat.kwargs["max_retries"] == 0
    assert chat.kwargs["timeout"] == 9
    assert chat.kwargs[key_arg].get_secret_value() == "secret"


def test_the_configured_provider_is_used_by_every_agent(monkeypatch):
    _fake_package(monkeypatch, "langchain_openai", "ChatOpenAI")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")

    for agent in (
        AnthropicExtractor(api_key="k"),
        AnthropicNarrator(api_key="k"),
        AnthropicQuestionWriter(api_key="k"),
    ):
        assert type(agent._runnable()).__name__ == "ChatOpenAI"


def test_a_missing_provider_package_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "langchain_openai", None)

    with pytest.raises(ImportError, match=r"poetry install -E openai"):
        llm_client.build_chat_model(
            model="m",
            api_key="k",
            temperature=0,
            max_tokens=1,
            timeout=1,
            provider="openai",
        )
    with pytest.raises(ImportError, match=r"poetry install -E openai"):
        llm_client.check_provider_installed("openai")


def test_an_unsupported_provider_is_refused():
    with pytest.raises(ValueError, match="unsupported"):
        llm_client.build_chat_model(
            model="m",
            api_key="k",
            temperature=0,
            max_tokens=1,
            timeout=1,
            provider="nope",
        )


def _settings_in_fresh_process(**env: str) -> subprocess.CompletedProcess[str]:
    clean = {
        k: v
        for k, v in os.environ.items()
        if k not in {*PROVIDERS_ENV, "LLM_PROVIDER", "ELICIT_MODEL"}
    }
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import dotenv; dotenv.load_dotenv = lambda *a, **k: False; "
                "from parkmind.config.settings import settings as s; "
                "print(s.LLM_PROVIDER, s.LLM_API_KEY_ENV, s.LLM_API_KEY, s.ELICIT_MODEL)"
            ),
        ],
        env={**clean, "PYTHONPATH": str(SRC), **env},
        capture_output=True,
        text=True,
        check=False,
        cwd="/",
    )


PROVIDERS_ENV = {key_env for key_env, _ in PROVIDERS.values()}


def test_anthropic_stays_the_default():
    out = _settings_in_fresh_process(ANTHROPIC_API_KEY="a-key")

    assert out.stdout.split() == [
        "anthropic",
        "ANTHROPIC_API_KEY",
        "a-key",
        PROVIDERS["anthropic"][1],
    ]


def test_another_provider_reads_its_own_key_and_default_model():
    out = _settings_in_fresh_process(
        LLM_PROVIDER="OpenAI", OPENAI_API_KEY="o-key", ANTHROPIC_API_KEY="a-key"
    )

    assert out.stdout.split() == [
        "openai",
        "OPENAI_API_KEY",
        "o-key",
        PROVIDERS["openai"][1],
    ]


def test_an_explicit_model_wins_over_the_provider_default():
    out = _settings_in_fresh_process(LLM_PROVIDER="google", ELICIT_MODEL="gemini-x")

    assert out.stdout.split()[-1] == "gemini-x"


def test_an_unknown_provider_stops_start_up():
    out = _settings_in_fresh_process(LLM_PROVIDER="nope")

    assert out.returncode != 0
    assert "LLM_PROVIDER" in out.stderr


def test_anthropic_errors_are_retryable_only_when_transient():
    anthropic = pytest.importorskip("anthropic")
    import httpx

    request = httpx.Request("POST", "https://x")
    rate = anthropic.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )
    bad = anthropic.BadRequestError(
        "no", response=httpx.Response(400, request=request), body=None
    )

    assert llm_client.is_retryable(rate, "anthropic")
    assert not llm_client.is_retryable(bad, "anthropic")


def test_google_retries_rate_limits_and_server_errors_but_not_other_client_errors():
    errors = pytest.importorskip("google.genai.errors")

    def make(cls: type, code: int) -> Exception:
        return cls(code, {"error": {"code": code, "message": "x", "status": "S"}})

    assert llm_client.is_retryable(make(errors.ClientError, 429), "google")
    assert llm_client.is_retryable(make(errors.ServerError, 503), "google")
    assert not llm_client.is_retryable(make(errors.ClientError, 400), "google")
