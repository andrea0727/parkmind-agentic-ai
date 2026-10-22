"""A node that fails is findable by name in the trace, still tied to its conversation."""

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode

from observability.obs_support import (
    ANTHROPIC_KEY,
    DATABASE_URL,
    LEVEL,
    SESSION,
    STATUS_MESSAGE,
    THREAD,
    Tracing,
    attribute_text,
    named,
    node_names,
    plan_scenario,
    planner,
    scripted_extractor,
)
from parkmind.graph import initial_planning_graph as ipg

THREAD_ID = "thread-errors"
SECRET_TEXT = f"cannot reach {DATABASE_URL} with {ANTHROPIC_KEY}"


def _failing_build_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: object, **__: object) -> None:
        raise RuntimeError(SECRET_TEXT)

    monkeypatch.setattr(ipg.BuildPlanUseCase, "build", fail)


@pytest.fixture
def failed(tracing: Tracing, monkeypatch: pytest.MonkeyPatch) -> list[ReadableSpan]:
    _failing_build_plan(monkeypatch)
    with pytest.raises(RuntimeError):
        plan_scenario(planner(scripted_extractor()), tracing.config(THREAD_ID))
    return tracing.spans()


def _errors(spans: list[ReadableSpan]) -> list[ReadableSpan]:
    return [s for s in spans if s.attributes.get(LEVEL) == "ERROR"]


def test_the_failing_node_has_an_error_span_carrying_the_error_type(
    failed: list[ReadableSpan],
) -> None:
    [span] = named(failed, "build_plan")

    assert span.attributes[LEVEL] == "ERROR"
    assert span.attributes[STATUS_MESSAGE] == "RuntimeError"
    assert span.status.status_code is StatusCode.ERROR


def test_the_innermost_error_span_names_the_responsible_node(
    failed: list[ReadableSpan],
) -> None:
    errors = _errors(failed)
    parent_ids = {s.parent.span_id for s in errors if s.parent}
    [innermost] = [s for s in errors if s.context.span_id not in parent_ids]

    assert innermost.name == "build_plan"
    assert {s.name for s in errors} >= {"build_plan", "downstream", "LangGraph"}


def test_the_nodes_before_the_failure_are_clean_and_none_after_it_ran(
    failed: list[ReadableSpan],
) -> None:
    ran = node_names(failed)

    assert ran[-1] == "build_plan"
    assert "check_plan" not in ran and "explain" not in ran
    clean = [s for s in failed if s.name in {"load_context", "resolve_group"}]
    assert clean and all(s.attributes.get(LEVEL) != "ERROR" for s in clean)


def test_the_failed_trace_stays_tied_to_the_thread(
    failed: list[ReadableSpan],
) -> None:
    for span in failed:
        assert span.attributes[SESSION] == THREAD_ID
        assert span.attributes[THREAD] == THREAD_ID


def test_the_errors_text_never_reaches_the_trace(failed: list[ReadableSpan]) -> None:
    text = attribute_text(failed)

    assert "cannot reach" not in text
    assert "hunter2-canary" not in text
    assert ANTHROPIC_KEY not in text
