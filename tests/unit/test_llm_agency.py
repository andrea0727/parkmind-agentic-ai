"""The LLMs get no agency: one output schema each, forced, and no real tools (§4).

Without tools there is no loop and nothing to call: the worst a model can do is fill
one form badly, and that form is validated (ADR 0004). A real tool, a second schema
or a free tool choice would break that, so it is pinned here for every LLM client.

The agentic context loader is the one LLM with real tools: read-only ones, from its
allowlist. When it lands, this file pins that it binds exactly that subset (ADR 0004, L10).
"""

import ast
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from parkmind.agents.ask_agent import AnthropicQuestionWriter
from parkmind.agents.ask_schema import QuestionDraft
from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.agents.elicit_schema import ElicitationExtraction
from parkmind.agents.explain_agent import AnthropicNarrator
from parkmind.agents.explain_schema import ExplanationDraft
from parkmind.agents.prompt_registry import BY_NAME, CAPABILITIES


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
    ("client", "schema", "name"),
    [
        (lambda: AnthropicExtractor(api_key="k"), ElicitationExtraction, "elicit"),
        (lambda: AnthropicNarrator(api_key="k"), ExplanationDraft, "explain"),
        (lambda: AnthropicQuestionWriter(api_key="k"), QuestionDraft, "ask"),
    ],
    ids=["elicit", "explain", "ask"],
)
def test_each_llm_gets_exactly_one_output_schema_forced_and_no_real_tool(
    client, schema, name
):
    client()._runnable()  # what every call goes through

    [(tools, choice)] = _RecordingChat.bound
    assert tools == [schema]
    assert isinstance(tools[0], type) and issubclass(tools[0], BaseModel)
    assert choice == schema.__name__
    assert BY_NAME[name].output_schema is schema  # the registry says what it binds


# Every module that builds or binds an LLM client, and so must be pinned by the test above.
# Only llm_client imports a provider package: the agents get their model from it.
PINNED_CLIENT_MODULES = {
    "agents/ask_agent.py",
    "agents/elicit_agent.py",
    "agents/explain_agent.py",
    "agents/llm_client.py",
}
_LLM_ENTRY_POINTS = {
    "ChatAnthropic",
    "ChatOpenAI",
    "ChatNVIDIA",
    "ChatGoogleGenerativeAI",
    "init_chat_model",
    "Anthropic",
    "AsyncAnthropic",
    "bind_tools",
    "with_structured_output",
}


# Imports that reach an LLM provider, whatever the call is named: any LangChain provider
# package (not ``langchain_core``), LangChain's model factories, and the provider SDKs.
_NOT_A_PROVIDER = frozenset(
    {"langchain_core", "langchain_protocol", "langchain_text_splitters"}
)
_PROVIDER_SDKS = (
    "anthropic",
    "openai",
    "litellm",
    "cohere",
    "mistralai",
    "google.generativeai",
    "google.genai",
)
_LANGCHAIN_MODEL_FACTORIES = (
    "langchain.chat_models",
    "langchain.llms",
    "langchain.embeddings",
)


def _reaches_a_provider(module: str) -> bool:
    root = module.split(".")[0]
    if root.startswith("langchain_"):
        return root not in _NOT_A_PROVIDER
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in (*_PROVIDER_SDKS, *_LANGCHAIN_MODEL_FACTORIES)
    )


def _imported_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.append(node.module)
    return modules


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def test_no_llm_client_is_built_outside_the_modules_this_file_pins():
    """A third LLM client (the agentic context loader, say) fails here until its
    binding is pinned above and its module added to ``PINNED_CLIENT_MODULES``."""
    source = Path(__file__).resolve().parents[2] / "src" / "parkmind"
    builders: set[str] = set()
    importers: set[str] = set()
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        name = path.relative_to(source).as_posix()
        if any(
            isinstance(node, ast.Call) and _called_name(node) in _LLM_ENTRY_POINTS
            for node in ast.walk(tree)
        ):
            builders.add(name)
        if any(_reaches_a_provider(m) for m in _imported_modules(tree)):
            importers.add(name)

    assert builders == PINNED_CLIENT_MODULES
    assert importers == {"agents/llm_client.py"}


def test_every_module_that_calls_an_llm_has_a_registered_prompt():
    """A new LLM client cannot land without its row in ``agents/prompt_registry.py``."""
    registered = [c.module for c in CAPABILITIES]

    assert len(registered) == len(set(registered))
    assert set(registered) == PINNED_CLIENT_MODULES - {"agents/llm_client.py"}


@pytest.mark.parametrize(
    ("module", "provider"),
    [
        ("langchain_aws", True),
        ("langchain_google_genai", True),
        ("langchain_openai.chat_models", True),
        ("litellm", True),
        ("openai", True),
        ("anthropic.types", True),
        ("google.generativeai", True),
        ("langchain.chat_models", True),
        ("langchain_core.messages", False),
        ("langfuse.langchain", False),
        ("google.protobuf", False),
    ],
)
def test_the_import_check_sees_any_provider_whatever_its_client_is_called(
    module, provider
):
    """ChatBedrock, ChatGoogleGenerativeAI or litellm.completion are caught by import."""
    assert _reaches_a_provider(module) is provider
