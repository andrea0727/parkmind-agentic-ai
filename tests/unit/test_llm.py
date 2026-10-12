"""core/llm.py: the model is configured as documented and the payload that reaches the API says so.

Offline: ``requests.Session.post`` is replaced, so nothing leaves the machine. One opt-in
live smoke test (``RUN_LIVE_LLM=1`` and a real key) checks the key and model really answer.
"""

import json
import os
import warnings
from typing import Any

import pytest
import requests

from parkmind.core.llm import get_llm
from parkmind.core.llm_retry import RetryLog, call_with_retries

_LIVE_KEY = os.environ.get("NVIDIA_API_KEY")
_LIVE_MODEL = os.environ.get("PARKMIND_LLM_MODEL")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARKMIND_LLM_MODEL", "nvidia/test-model")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake-key")


class FakeResponse:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {}
        self._body = body if body is not None else {"detail": "boom"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self._body


def completion(text: str = "pong", tool_calls: list[dict[str, Any]] | None = None) -> FakeResponse:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return FakeResponse(
        200,
        {"id": "c1", "choices": [{"index": 0, "message": message, "finish_reason": "stop"}], "usage": {}},
    )


class Api:
    """Stands in for the NVIDIA endpoint: answers with the queued responses, records requests."""

    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def post(self, _session: Any, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"url": url, **kwargs})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Api:
    fake = Api(completion())
    monkeypatch.setattr(requests.Session, "post", lambda self, url, **kw: fake.post(self, url, **kw))
    return fake


def _llm(**kwargs: Any) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return get_llm(**kwargs)


def test_get_llm_reads_the_model_and_key_from_the_environment() -> None:
    llm = _llm()

    assert llm.model == "nvidia/test-model"
    assert llm._client.api_key.get_secret_value() == "nvapi-fake-key"


@pytest.mark.parametrize("missing", ["PARKMIND_LLM_MODEL", "NVIDIA_API_KEY"])
def test_get_llm_requires_both_environment_variables(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)

    with pytest.raises(KeyError, match=missing):
        get_llm()


def test_get_llm_defaults() -> None:
    llm = _llm()

    assert llm.temperature == 1
    assert llm.top_p == 0.95
    assert llm.max_tokens == 16384


def test_get_llm_accepts_a_temperature_and_a_token_limit() -> None:
    llm = _llm(temperature=0.2, max_tokens=512)

    assert llm.temperature == 0.2
    assert llm.max_tokens == 512


def test_reasoning_options_are_kept_in_model_kwargs() -> None:
    assert _llm().model_kwargs == {
        "reasoning_budget": 16384,
        "chat_template_kwargs": {"enable_thinking": True},
    }


def test_the_request_carries_the_model_sampling_and_thinking_options(api: Api) -> None:
    reply = _llm(temperature=0.5, max_tokens=256).invoke("ping")

    assert reply.content == "pong"
    (request,) = api.requests
    assert request["url"].endswith("/chat/completions")
    payload = request["json"]
    assert payload["model"] == "nvidia/test-model"
    assert payload["temperature"] == 0.5
    assert payload["max_tokens"] == 256
    assert payload["top_p"] == 0.95
    assert payload["reasoning_budget"] == 16384
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}
    assert payload["stream"] is False
    assert payload["messages"] == [{"role": "user", "content": "ping"}]


def test_the_request_is_authenticated_and_given_a_timeout(api: Api) -> None:
    _llm().invoke("ping")

    (request,) = api.requests
    assert request["headers"]["Authorization"] == "Bearer nvapi-fake-key"
    assert request["timeout"] == 180


def test_the_api_key_is_not_part_of_the_payload(api: Api) -> None:
    _llm().invoke("ping")

    assert "nvapi-fake-key" not in json.dumps(api.requests[0]["json"])


@pytest.mark.filterwarnings("ignore:Model .* is not known to support tools")
def test_a_forced_tool_call_comes_back_as_tool_calls(api: Api) -> None:
    from parkmind.agents.elicit_schema import ElicitationExtraction

    api.responses = [
        completion(
            "",
            [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {
                        "name": ElicitationExtraction.__name__,
                        "arguments": json.dumps({"party_size": 2}),
                    },
                }
            ],
        )
    ]
    llm = _llm().bind_tools([ElicitationExtraction], tool_choice=ElicitationExtraction.__name__)

    reply = llm.invoke("two of us")

    assert reply.tool_calls[0]["name"] == ElicitationExtraction.__name__
    assert reply.tool_calls[0]["args"] == {"party_size": 2}
    sent = api.requests[0]["json"]
    assert sent["tool_choice"]["function"]["name"] == ElicitationExtraction.__name__


def test_a_server_error_surfaces_with_its_status(api: Api) -> None:
    api.responses = [FakeResponse(500, {"status": 500, "title": "Internal Server Error"})]

    with pytest.raises(Exception, match=r"\[500\]"):
        _llm().invoke("ping")


def test_retries_ride_out_server_errors_from_the_real_client(api: Api) -> None:
    api.responses = [
        FakeResponse(500, {"status": 500, "title": "Internal Server Error"}),
        FakeResponse(503, {"status": 503, "title": "Service Unavailable"}),
        completion("back"),
    ]
    log = RetryLog()
    llm = _llm()

    reply = call_with_retries(lambda: llm.invoke("ping"), log=log, sleep=lambda _s: None)

    assert reply.content == "back"
    assert len(api.requests) == 3
    assert log.retries == 2


def test_a_rejected_key_is_not_retried(api: Api) -> None:
    api.responses = [FakeResponse(401, {"status": 401, "title": "Unauthorized"})]
    llm = _llm()

    with pytest.raises(Exception, match=r"\[401\]"):
        call_with_retries(lambda: llm.invoke("ping"), sleep=lambda _s: None)

    assert len(api.requests) == 1


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM") != "1" or not (_LIVE_KEY and _LIVE_MODEL),
    reason="live smoke test: set RUN_LIVE_LLM=1 with NVIDIA_API_KEY and PARKMIND_LLM_MODEL in .env",
)
def test_live_the_configured_model_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", str(_LIVE_KEY))
    monkeypatch.setenv("PARKMIND_LLM_MODEL", str(_LIVE_MODEL))

    reply = call_with_retries(lambda: _llm(max_tokens=2048).invoke("Reply with the single word: pong"))

    assert "pong" in str(reply.content).lower()
