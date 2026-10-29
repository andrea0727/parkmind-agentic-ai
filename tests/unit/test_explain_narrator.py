"""The Claude narrator of EXPLAIN: no key, no call."""

from typing import Any, ClassVar

import pytest

from parkmind.agents.explain_agent import (
    EXPLAIN_SDK_RETRIES,
    EXPLAIN_TIMEOUT_SECONDS,
    AnthropicNarrator,
    NarratorOutputError,
    NarratorUnavailableError,
)
from parkmind.agents.explain_prompt import EXPLAIN_PROMPT_VERSION, EXPLAIN_SYSTEM_PROMPT


def test_without_an_api_key_nothing_is_sent():
    with pytest.raises(NarratorUnavailableError):
        AnthropicNarrator(api_key="").narrate('{"stops": []}')


def test_the_request_bounds_come_from_settings_and_a_retry_is_not_doubled():
    from parkmind.config.settings import settings

    assert settings.EXPLAIN_TIMEOUT_SECONDS == EXPLAIN_TIMEOUT_SECONDS > 0
    assert settings.EXPLAIN_SDK_RETRIES == EXPLAIN_SDK_RETRIES


def test_the_prompt_is_versioned_and_states_the_grounding_and_labelling_rules():
    assert EXPLAIN_PROMPT_VERSION == "explain-v3"
    for rule in (
        "exactly as given",
        "(stated)",
        "(learned)",
        "unmet_must_do",
        "default",
    ):
        assert rule in EXPLAIN_SYSTEM_PROMPT, rule


# --- the real narrator over a fake client (review #90) ------------------------------


class _Response:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


class _FakeChat:
    """Stands in for ChatAnthropic: records how it was built and what it was sent."""

    built: ClassVar[dict[str, Any]] = {}
    sent: ClassVar[list[Any]] = []
    answer: _Response = _Response([{"args": {"text": "ok"}}])

    def __init__(self, **kwargs):
        _FakeChat.built = kwargs

    def bind_tools(self, tools, tool_choice):
        return self

    def invoke(self, messages):
        _FakeChat.sent.append(messages)
        return _FakeChat.answer


@pytest.fixture
def fake_chat(monkeypatch):
    import langchain_anthropic

    _FakeChat.sent = []
    _FakeChat.answer = _Response([{"args": {"text": "ok"}}])
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _FakeChat)
    return _FakeChat


def test_every_request_is_bounded_in_time_and_retries(fake_chat):
    AnthropicNarrator(api_key="k").narrate("{}")

    assert fake_chat.built["timeout"] == EXPLAIN_TIMEOUT_SECONDS
    assert fake_chat.built["max_retries"] == 0  # retried by the counted loop [G12]
    assert fake_chat.built["temperature"] == 0


@pytest.mark.parametrize(
    "calls", [[], [{"args": "not a mapping"}]], ids=["no_tool_call", "malformed_args"]
)
def test_an_answer_without_structured_output_is_an_output_error(fake_chat, calls):
    fake_chat.answer = _Response(calls)

    with pytest.raises(NarratorOutputError):
        AnthropicNarrator(api_key="k").narrate("{}")


def test_the_repair_hint_is_added_to_the_message(fake_chat):
    AnthropicNarrator(api_key="k").narrate(
        '{"stops": []}', "These numbers are not in the payload: 45"
    )

    [_system, human] = fake_chat.sent[0]
    assert '{"stops": []}' in human.content
    assert "These numbers are not in the payload: 45" in human.content
