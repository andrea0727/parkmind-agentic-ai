"""The LLMs get no agency: one output schema each, forced, and no real tools (§4).

Without tools there is no loop and nothing to call: the worst a model can do is fill
one form badly, and that form is validated (ADR 0004). A real tool, a second schema
or a free tool choice would break that, so it is pinned here for every LLM client.
"""

from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.agents.elicit_schema import ElicitationExtraction
from parkmind.agents.explain_agent import AnthropicNarrator
from parkmind.agents.explain_schema import ExplanationDraft


class _RecordingChat:
    """Stands in for ChatAnthropic and records what the client binds."""

    bound: ClassVar[list[tuple[list[Any], Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        pass

    def bind_tools(self, tools: list[Any], tool_choice: Any = None) -> "_RecordingChat":
        _RecordingChat.bound.append((list(tools), tool_choice))
        return self


@pytest.fixture(autouse=True)
def recording_chat(monkeypatch):
    import langchain_anthropic

    _RecordingChat.bound = []
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RecordingChat)


@pytest.mark.parametrize(
    ("client", "schema"),
    [
        (lambda: AnthropicExtractor(api_key="k"), ElicitationExtraction),
        (lambda: AnthropicNarrator(api_key="k"), ExplanationDraft),
    ],
    ids=["elicit", "explain"],
)
def test_each_llm_gets_exactly_one_output_schema_forced_and_no_real_tool(client, schema):
    client()._runnable()  # what every call goes through

    [(tools, choice)] = _RecordingChat.bound
    assert tools == [schema]
    assert isinstance(tools[0], type) and issubclass(tools[0], BaseModel)
    assert choice == schema.__name__
