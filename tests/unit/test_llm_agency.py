"""The LLMs get no agency: one output schema each, forced, and no real tools (§4).

Without tools there is no loop and nothing to call: the worst a model can do is fill
one form badly, and that form is validated (ADR 0004). A real tool, a second schema
or a free tool choice would break that, so it is pinned here for every LLM client.

P1-22's context loader will be the one LLM with real tools: read-only ones, from its
allowlist. When it lands, this file pins that it binds exactly that subset (ADR 0004, L10).
"""

import ast
from pathlib import Path
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
def test_each_llm_gets_exactly_one_output_schema_forced_and_no_real_tool(
    client, schema
):
    client()._runnable()  # what every call goes through

    [(tools, choice)] = _RecordingChat.bound
    assert tools == [schema]
    assert isinstance(tools[0], type) and issubclass(tools[0], BaseModel)
    assert choice == schema.__name__


# Every module that builds an LLM client, and so must be pinned by the test above.
PINNED_CLIENT_MODULES = {"agents/elicit_agent.py", "agents/explain_agent.py"}
_LLM_ENTRY_POINTS = {
    "ChatAnthropic",
    "ChatOpenAI",
    "ChatNVIDIA",
    "init_chat_model",
    "Anthropic",
    "AsyncAnthropic",
    "bind_tools",
    "with_structured_output",
}


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def test_no_llm_client_is_built_outside_the_modules_this_file_pins():
    """A third LLM client (the P1-22 context loader, say) fails here until its
    binding is pinned above and its module added to ``PINNED_CLIENT_MODULES``."""
    source = Path(__file__).resolve().parents[2] / "src" / "parkmind"
    builders: set[str] = set()
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call) and _called_name(node) in _LLM_ENTRY_POINTS
            for node in ast.walk(tree)
        ):
            builders.add(path.relative_to(source).as_posix())

    assert builders == PINNED_CLIENT_MODULES
