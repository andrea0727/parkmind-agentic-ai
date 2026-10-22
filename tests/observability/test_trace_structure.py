"""One planning conversation, traced through the production Langfuse path, read as spans."""

import pytest
from opentelemetry.sdk.trace import ReadableSpan

from observability.obs_support import (
    LEVEL,
    OBSERVATION_TYPE,
    SESSION,
    STATUS_MESSAGE,
    THREAD,
    Tracing,
    claude_planner,
    duration_seconds,
    named,
    node_names,
    plan_scenario,
    trace_ids,
)

THREAD_ID = "thread-structure"
NODES_IN_ORDER = [
    "elicit",
    "confirm_hard_constraints",
    "confirm_hard_constraints",
    "validate_constraints",
    "downstream",
    "load_context",
    "resolve_group",
    "build_plan",
    "check_plan",
    "explain",
    "propose_plan",
    "interrupt_approval",
]


@pytest.fixture
def spans(tracing: Tracing, monkeypatch: pytest.MonkeyPatch) -> list[ReadableSpan]:
    plan_scenario(claude_planner(monkeypatch), tracing.config(THREAD_ID))
    return tracing.spans()


def _roots(spans: list[ReadableSpan]) -> list[ReadableSpan]:
    return [s for s in spans if s.parent is None]


def test_every_graph_node_that_ran_has_a_span_in_execution_order(
    spans: list[ReadableSpan],
) -> None:
    assert node_names(spans) == NODES_IN_ORDER


def test_each_graph_run_is_one_trace_with_a_single_root(
    spans: list[ReadableSpan],
) -> None:
    roots = _roots(spans)

    assert len(trace_ids(spans)) == 2  # the first turn, then the confirmation
    assert {r.name for r in roots} == {"LangGraph"}
    assert len({r.context.trace_id for r in roots}) == 2
    assert all(
        sum(1 for r in roots if r.context.trace_id == trace) == 1
        for trace in trace_ids(spans)
    )


def test_every_span_is_inside_its_parents_time_window(
    spans: list[ReadableSpan],
) -> None:
    by_id = {s.context.span_id: s for s in spans}
    for span in spans:
        assert duration_seconds(span) >= 0
        if span.parent is not None and span.parent.span_id in by_id:
            parent = by_id[span.parent.span_id]
            assert parent.start_time <= span.start_time
            assert span.end_time <= parent.end_time


def test_the_planning_nodes_hang_under_the_downstream_stage(
    spans: list[ReadableSpan],
) -> None:
    [stage] = named(spans, "downstream")
    [stage_graph] = [
        s
        for s in spans
        if s.parent is not None and s.parent.span_id == stage.context.span_id
    ]
    stage_nodes = [
        s.name
        for s in spans
        if s.parent is not None and s.parent.span_id == stage_graph.context.span_id
    ]

    assert stage_nodes[:4] == [
        "load_context",
        "resolve_group",
        "build_plan",
        "check_plan",
    ]
    assert {"explain", "propose_plan", "interrupt_approval"} <= set(stage_nodes)


def test_a_graph_paused_for_the_guests_is_not_traced_as_an_error(
    spans: list[ReadableSpan],
) -> None:
    [paused, *_] = named(spans, "confirm_hard_constraints")

    assert paused.attributes[STATUS_MESSAGE] == "GraphInterrupt"
    assert paused.attributes[LEVEL] == "DEFAULT"
    assert not [s for s in spans if s.attributes.get(LEVEL) == "ERROR"]


def test_every_span_carries_the_thread_as_its_session(
    spans: list[ReadableSpan],
) -> None:
    for span in spans:
        assert span.attributes[SESSION] == THREAD_ID
        assert span.attributes[THREAD] == THREAD_ID
        assert span.attributes["langfuse.trace.tags"] == ("initial",)


def test_every_node_is_traced_as_a_chain_and_the_model_calls_as_generations(
    spans: list[ReadableSpan],
) -> None:
    kinds = {
        s.name: s.attributes[OBSERVATION_TYPE]
        for s in spans
        if s.name in set(NODES_IN_ORDER)
    }
    generation_nodes = [
        s.attributes["langfuse.observation.metadata.langgraph_node"]
        for s in spans
        if s.attributes[OBSERVATION_TYPE] == "generation"
    ]

    assert set(kinds.values()) == {"chain"}
    assert generation_nodes == ["elicit", "explain"]
