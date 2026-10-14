"""C19 canary: a real graph run, traced to Langfuse, exports none of the guests' words.

The elicitation graph runs with the real ``AnthropicExtractor`` (only the chat model
is a fake, so a real LLM generation is traced with its prompt) and the real Langfuse
``CallbackHandler``, on a client built like ``langfuse_callbacks`` builds it but
exporting to memory: every span that would reach Langfuse is inspected, offline.
"""

import copy
import itertools
import json
from typing import Any

import pytest
from elicit_support import make_intake, make_names, scenario
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from langgraph.types import Command
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.elicitation_graph import build_elicitation_graph
from parkmind.services.clients.observability.langfuse_tracing import mask_for_trace

COMPLETE = scenario("wiki_example_complete")
GUEST_WORDS = "zebracanary"
CORRECTION = "zebracorrection"
LABEL = "Zebrina"
ACCESSIBILITY_VALUES = ("LIMITED_WALKING", "mobility_requirements")
PUBLIC_KEY = "pk-canary"


class _ToolCallingFake(GenericFakeChatModel):
    """A chat model that 'calls' the forced schema; binding tools is a no-op."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ToolCallingFake":
        return self


def _extraction() -> dict[str, Any]:
    extraction = copy.deepcopy(COMPLETE["extraction"])
    extraction["guests"][0]["label"] = LABEL
    return extraction


@pytest.fixture
def exported(monkeypatch):
    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key=PUBLIC_KEY,
        secret_key="sk-canary",
        base_url="http://localhost:1",
        mask=mask_for_trace,
        span_exporter=exporter,
        tracer_provider=TracerProvider(),
    )
    reply = AIMessage(
        content="",
        tool_calls=[
            {"name": "ElicitationExtraction", "args": _extraction(), "id": "c1"}
        ],
    )
    import langchain_anthropic

    monkeypatch.setattr(
        langchain_anthropic,
        "ChatAnthropic",
        lambda **_: _ToolCallingFake(messages=itertools.repeat(reply)),
    )
    yield exporter, client
    client.shutdown()


def _span_text(exporter: InMemorySpanExporter) -> tuple[list[Any], str]:
    spans = list(exporter.get_finished_spans())
    text = json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes or {}),
                "events": [dict(e.attributes or {}) for e in span.events],
            }
            for span in spans
        ],
        default=str,
    )
    return spans, text


def test_a_traced_conversation_exports_none_of_the_guests_words(exported):
    exporter, client = exported
    intake, _ = make_intake()
    graph = build_elicitation_graph(
        AnthropicExtractor(api_key="k"),
        intake,
        default_checkpointer(),
        lambda state: {},
        names=make_names(),
    )
    config: Any = {
        "configurable": {"thread_id": "t1"},
        "callbacks": [CallbackHandler(public_key=PUBLIC_KEY)],
        "metadata": {"langfuse_session_id": "t1", "langfuse_tags": ["initial"]},
    }
    messages = [*COMPLETE["messages"], f"I'm {LABEL}, and {GUEST_WORDS} matters."]

    first = graph.invoke(
        {"thread_id": "t1", "messages": [HumanMessage(content=m) for m in messages]},
        config=config,
    )
    assert first["__interrupt__"], "the run should stop at the confirmation"
    graph.invoke(
        Command(resume={"confirmed": False, "correction": CORRECTION}), config=config
    )
    client.flush()

    spans, text = _span_text(exporter)
    # Not vacuous: the graph, its nodes and the LLM generation were traced, masked.
    assert len(spans) >= 3
    assert "langfuse.observation.type" in text and "generation" in text
    assert "elicit" in text
    assert "[masked]" in text
    assert "t1" in text  # the session is the thread
    # And nothing the guests wrote, nor their accessibility, left the process.
    for secret in (
        GUEST_WORDS,
        CORRECTION,
        LABEL,
        "My father can't walk",
        *ACCESSIBILITY_VALUES,
    ):
        assert secret not in text, f"{secret!r} reached an exported span"
