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
from parkmind.services.clients.observability.langfuse_tracing import (
    _masked_handler_class,
    mask_for_trace,
)

COMPLETE = scenario("wiki_example_complete")
GUEST_WORDS = "zebracanary"
CORRECTION = "zebracorrection"
LABEL = "Zebrina"
ACCESSIBILITY_VALUES = ("LIMITED_WALKING", "mobility_requirements")
UNKNOWN_RIDE = "Zebracoaster Pirates"
PLAIN_RESUME = "zebraresume is what I meant"


class _ToolCallingFake(GenericFakeChatModel):
    """A chat model that 'calls' the forced schema; binding tools is a no-op."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ToolCallingFake":
        return self


def _extraction() -> dict[str, Any]:
    extraction = copy.deepcopy(COMPLETE["extraction"])
    extraction["guests"][0]["label"] = LABEL
    return extraction


def _install_chat_model(monkeypatch, extraction: dict[str, Any]) -> None:
    reply = AIMessage(
        content="",
        tool_calls=[{"name": "ElicitationExtraction", "args": extraction, "id": "c1"}],
    )
    import langchain_anthropic

    monkeypatch.setattr(
        langchain_anthropic,
        "ChatAnthropic",
        lambda **_: _ToolCallingFake(messages=itertools.repeat(reply)),
    )


@pytest.fixture
def exported(monkeypatch, request):
    exporter = InMemorySpanExporter()
    public_key = f"pk-canary-{request.node.name}"
    client = Langfuse(
        public_key=public_key,
        secret_key="sk-canary",
        base_url="http://localhost:1",
        mask=mask_for_trace,
        span_exporter=exporter,
        tracer_provider=TracerProvider(),
    )
    _install_chat_model(monkeypatch, _extraction())
    yield exporter, client, public_key
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
    exporter, client, public_key = exported
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
        "callbacks": [CallbackHandler(public_key=public_key)],
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


def _unknown_attraction_trace(
    exporter: InMemorySpanExporter, client: Langfuse, handler: Any
) -> str:
    """Run until the graph asks about an unknown must-do, answer in plain text, export."""
    intake, _ = make_intake()
    graph = build_elicitation_graph(
        AnthropicExtractor(api_key="k"),
        intake,
        default_checkpointer(),
        lambda state: {},
        names=make_names(),
    )
    config: Any = {"configurable": {"thread_id": "t2"}, "callbacks": [handler]}
    first = graph.invoke(
        {
            "thread_id": "t2",
            "messages": [HumanMessage(content=m) for m in COMPLETE["messages"]],
        },
        config=config,
    )
    assert first["__interrupt__"][0].value["missing"] == [
        f"unknown_attraction:{UNKNOWN_RIDE}"
    ]
    graph.invoke(Command(resume=PLAIN_RESUME), config=config)
    client.flush()
    return _span_text(exporter)[1]


@pytest.fixture
def exported_unknown_must_do(monkeypatch, exported):
    _install_chat_model(monkeypatch, {**_extraction(), "must_do": [UNKNOWN_RIDE]})
    return exported


def test_an_unknown_must_do_and_a_plain_text_answer_never_reach_a_span(
    exported_unknown_must_do,
):
    exporter, client, public_key = exported_unknown_must_do
    handler = _masked_handler_class()(public_key=public_key)

    text = _unknown_attraction_trace(exporter, client, handler)

    assert "Interrupt" in text, "the paused run's error status should be traced by type"
    assert "Zebracoaster" not in text, "the guests' attraction name reached a span"
    assert "zebraresume" not in text, "the plain-text answer reached a span"


def test_the_sdks_plain_handler_leaks_the_unknown_name_in_an_error_status(
    exported_unknown_must_do,
):
    """Why ``_masked_handler_class`` exists: the same run fails with the SDK's own handler."""
    exporter, client, public_key = exported_unknown_must_do

    text = _unknown_attraction_trace(
        exporter, client, CallbackHandler(public_key=public_key)
    )

    assert "Zebracoaster" in text
