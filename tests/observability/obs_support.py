"""Offline tracing harness: the production Langfuse path exporting to memory."""

import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, ClassVar

import langchain_anthropic
import langfuse
import pytest
from elicit_support import FakeExtractor, make_intake, make_names, scenario
from explain_support import NoNarrator
from fakes import InMemorySnapshotRepository
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from planning_support import (
    NOW,
    factory_for,
    make_deps,
    seed_snapshot,
    snapshot_context,
)

from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.agents.explain_agent import AnthropicNarrator
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.services.clients.observability import langfuse_tracing

OBSERVATION_TYPE = "langfuse.observation.type"
LEVEL = "langfuse.observation.level"
STATUS_MESSAGE = "langfuse.observation.status_message"
INPUT = "langfuse.observation.input"
OUTPUT = "langfuse.observation.output"
SESSION = "session.id"
THREAD = "langfuse.trace.metadata.thread_id"
MODEL = "langfuse.observation.model.name"
USAGE = "langfuse.observation.usage_details"
LANGGRAPH_NODE = "langfuse.observation.metadata.langgraph_node"

ENV = {
    "PARKMIND_TRACING": "langfuse",
    "LANGFUSE_SECRET_KEY": "sk-lf-offline",
    "LANGFUSE_BASE_URL": "http://localhost:1",
}
USAGE_TOKENS = {"input_tokens": 1234, "output_tokens": 567, "total_tokens": 1801}
ANTHROPIC_KEY = "sk-" + "ant-api03-OBSERVABILITYCANARY"
DATABASE_URL = (
    "postgresql" + "://parkmind:hunter2-canary@db.internal:5432/parkmind"
)
MODEL_NAME = "claude-haiku-4-5-20251001"
LLM_DELAY_SECONDS = 0.02


class RaisingExporter(SpanExporter):
    """An exporter whose every export fails, like an unreachable Langfuse."""

    def __init__(self) -> None:
        self.attempts = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.attempts += 1
        raise ConnectionError("langfuse is unreachable")


@dataclass
class Tracing:
    """The callbacks ``langfuse_callbacks`` builds, with the spans they export."""

    exporter: SpanExporter
    env: dict[str, str]
    callbacks: list[Any] = field(default_factory=list)

    def config(self, thread_id: str) -> Any:
        return {
            "configurable": {"thread_id": thread_id},
            "callbacks": self.callbacks,
            "metadata": {
                "langfuse_session_id": thread_id,
                "langfuse_tags": ["initial"],
            },
        }

    def flush(self) -> None:
        assert langfuse_tracing._client is not None
        langfuse_tracing._client.flush()

    def spans(self) -> list[ReadableSpan]:
        self.flush()
        assert isinstance(self.exporter, InMemorySpanExporter)
        return sorted(
            self.exporter.get_finished_spans(), key=lambda s: s.start_time or 0
        )


@contextmanager
def tracing_to(
    monkeypatch: pytest.MonkeyPatch,
    exporter: SpanExporter | None = None,
    *,
    mask: Callable[..., Any] | None = None,
) -> Iterator[Tracing]:
    """Switch Langfuse tracing on through ``langfuse_callbacks``, exporting to ``exporter``."""
    exporter = exporter or InMemorySpanExporter()
    real_client = langfuse.Langfuse

    def client(**kwargs: Any) -> Any:
        if mask is not None:
            kwargs["mask"] = mask
        return real_client(
            **kwargs, span_exporter=exporter, tracer_provider=TracerProvider()
        )

    monkeypatch.setattr(langfuse, "Langfuse", client)
    monkeypatch.setattr(langfuse_tracing, "_client", None)
    env = {**ENV, "LANGFUSE_PUBLIC_KEY": f"pk-lf-{uuid.uuid4().hex}"}
    try:
        yield Tracing(exporter, env, langfuse_tracing.langfuse_callbacks(env))
    finally:
        langfuse_tracing.flush_tracing()


class RecordedClaude(langchain_anthropic.ChatAnthropic):
    """The real ``ChatAnthropic`` with its network call replaced by a canned reply."""

    replies: ClassVar[dict[str, Callable[[], Mapping[str, Any]]]] = {}
    usage: ClassVar[dict[str, int] | None] = None

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        time.sleep(LLM_DELAY_SECONDS)
        tool = kwargs.get("tool_choice", {}).get("name", "")
        reply = AIMessage(
            content="",
            tool_calls=[{"name": tool, "args": self.replies[tool](), "id": "toolu_1"}],
            usage_metadata=self.usage,  # type: ignore[arg-type]
            response_metadata={"model_name": self.model},
        )
        return ChatResult(generations=[ChatGeneration(message=reply)])


def claude_replies(
    monkeypatch: pytest.MonkeyPatch,
    usage: dict[str, int] | None = USAGE_TOKENS,
    **replies: Callable[[], Mapping[str, Any]],
) -> None:
    """Every ``ChatAnthropic`` the agents build answers its forced tool with ``replies``."""
    monkeypatch.setattr(RecordedClaude, "replies", dict(replies))
    monkeypatch.setattr(RecordedClaude, "usage", usage)
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", RecordedClaude)


def claude_planner(
    monkeypatch: pytest.MonkeyPatch,
    scenario_id: str = "wiki_example_complete",
    *,
    extraction: Mapping[str, Any] | None = None,
    usage: dict[str, int] | None = USAGE_TOKENS,
) -> Any:
    """The planning graph with the real Claude extractor and narrator on a recorded model."""
    reply = (
        extraction if extraction is not None else scenario(scenario_id)["extraction"]
    )
    claude_replies(
        monkeypatch,
        usage,
        ElicitationExtraction=lambda: reply,
        ExplanationDraft=lambda: {"text": "Your plan keeps the walking short."},
    )
    return planner(
        AnthropicExtractor(api_key=ANTHROPIC_KEY),
        narrator=AnthropicNarrator(api_key=ANTHROPIC_KEY),
    )


def planner(
    extractor: Any,
    *,
    narrator: Any = None,
    deps_factory: Any = None,
) -> Any:
    """The initial planning graph on in-memory ports, as the quality tests build it."""
    intake, sessions = make_intake()
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context(retrieved_at=NOW - timedelta(minutes=5)))
    deps = make_deps(sessions=sessions, snapshots=snapshots)
    return ipg.build_initial_planning_graph(
        extractor,
        intake,
        default_checkpointer(),
        deps_factory=deps_factory or factory_for(deps),
        clock=lambda: NOW,
        names=make_names(),
        narrator=narrator or NoNarrator(),
    )


def plan_scenario(
    graph: Any, config: Any, scenario_id: str = "wiki_example_complete"
) -> dict[str, Any]:
    """Run to the first question, then confirm; returns the state after the second run."""
    thread_id = config["configurable"]["thread_id"]
    messages = scenario(scenario_id)["messages"]
    graph.invoke(
        {
            "thread_id": thread_id,
            "messages": [HumanMessage(content=m) for m in messages],
        },
        config=config,
    )
    return graph.invoke(
        Command(resume={"confirmed": True, "consent": True}), config=config
    )


def scripted_extractor(scenario_id: str = "wiki_example_complete") -> FakeExtractor:
    return FakeExtractor(scenario(scenario_id)["extraction"])


def named(spans: Sequence[ReadableSpan], name: str) -> list[ReadableSpan]:
    return [s for s in spans if s.name == name]


def node_names(spans: Sequence[ReadableSpan]) -> list[str]:
    """The LangGraph nodes that ran, in start order (routers and graph roots excluded)."""
    return [
        str(s.attributes[LANGGRAPH_NODE])
        for s in spans
        if s.name == (s.attributes or {}).get(LANGGRAPH_NODE)
    ]


def children(spans: Sequence[ReadableSpan], parent: ReadableSpan) -> list[ReadableSpan]:
    span_id = parent.context.span_id if parent.context else None
    return [s for s in spans if s.parent is not None and s.parent.span_id == span_id]


def generations(spans: Sequence[ReadableSpan]) -> list[ReadableSpan]:
    return [
        s for s in spans if (s.attributes or {}).get(OBSERVATION_TYPE) == "generation"
    ]


def attribute_text(spans: Sequence[ReadableSpan]) -> str:
    """Every attribute and event of every span, as one string to search."""
    parts = []
    for span in spans:
        parts.append(span.name)
        parts.extend(f"{k}={v}" for k, v in (span.attributes or {}).items())
        for event in span.events:
            parts.append(event.name)
            parts.extend(f"{k}={v}" for k, v in (event.attributes or {}).items())
        parts.append(str(span.status.description))
    return "\n".join(parts)


def duration_seconds(span: ReadableSpan) -> float:
    assert span.start_time is not None and span.end_time is not None
    return (span.end_time - span.start_time) / 1e9


def trace_ids(spans: Sequence[ReadableSpan]) -> set[int]:
    return {s.context.trace_id for s in spans if s.context}
