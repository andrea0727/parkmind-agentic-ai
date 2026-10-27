"""Tracing is opt-in and never in the way: off by default, LangSmith refused, failures contained."""

from typing import Any

import pytest

from observability.obs_support import (
    RaisingExporter,
    plan_scenario,
    planner,
    scripted_extractor,
    tracing_to,
)
from parkmind.config.tracing import UnmaskedTracingError
from parkmind.services.clients.observability import langfuse_tracing
from parkmind.services.clients.observability.langfuse_tracing import (
    TracingConfigError,
    langfuse_callbacks,
)

THREAD_ID = "thread-switch"
LANGFUSE_KEYS = {
    "LANGFUSE_PUBLIC_KEY": "pk-lf-offline",
    "LANGFUSE_SECRET_KEY": "sk-lf-offline",
    "LANGFUSE_BASE_URL": "http://localhost:1",
}


def _outcome(state: dict[str, Any]) -> tuple[str, list[str]]:
    [interrupt] = state["__interrupt__"]
    stops = [stop.node_id for stop in interrupt.value["candidate_plan"].stops]
    return interrupt.value["kind"], stops


def _untraced_outcome() -> tuple[str, list[str]]:
    config = {"configurable": {"thread_id": THREAD_ID}}
    return _outcome(plan_scenario(planner(scripted_extractor()), config))


@pytest.fixture(autouse=True)
def no_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(langfuse_tracing, "_client", None)
    yield
    langfuse_tracing.flush_tracing()


@pytest.mark.parametrize(
    "env", [{}, LANGFUSE_KEYS, {**LANGFUSE_KEYS, "PARKMIND_TRACING": "off"}]
)
def test_without_the_tracing_switch_no_handler_and_no_client_are_created(
    env: dict[str, str],
) -> None:
    assert langfuse_callbacks(env) == []
    assert langfuse_tracing._client is None


def test_an_untraced_run_plans_exactly_as_a_traced_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    untraced = _untraced_outcome()
    with tracing_to(monkeypatch) as tracing:
        config = tracing.config(THREAD_ID)
        traced = _outcome(plan_scenario(planner(scripted_extractor()), config))

    assert traced == untraced
    assert untraced[0] == "plan_approval" and untraced[1]


def test_the_switch_on_without_its_settings_stops_the_app_instead_of_tracing_nothing() -> (
    None
):
    with pytest.raises(TracingConfigError, match="LANGFUSE_PUBLIC_KEY"):
        langfuse_callbacks({"PARKMIND_TRACING": "langfuse"})


@pytest.mark.parametrize("switch", ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"])
def test_langsmith_tracing_is_refused_before_any_graph_runs(
    monkeypatch: pytest.MonkeyPatch, switch: str
) -> None:
    monkeypatch.setenv(switch, "true")

    with pytest.raises(UnmaskedTracingError, match=switch):
        planner(scripted_extractor())


def test_langsmith_is_still_refused_while_langfuse_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tracing_to(monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")

        with pytest.raises(UnmaskedTracingError):
            planner(scripted_extractor())


def test_langsmith_cannot_be_chosen_as_the_tracing_backend() -> None:
    with pytest.raises(TracingConfigError, match="langsmith"):
        langfuse_callbacks({**LANGFUSE_KEYS, "PARKMIND_TRACING": "langsmith"})


def test_an_unreachable_exporter_does_not_change_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    untraced = _untraced_outcome()
    exporter = RaisingExporter()
    with tracing_to(monkeypatch, exporter) as tracing:
        config = tracing.config(THREAD_ID)
        traced = _outcome(plan_scenario(planner(scripted_extractor()), config))
        tracing.flush()

    assert exporter.attempts > 0
    assert traced == untraced


def test_a_mask_that_fails_hides_everything_and_does_not_break_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*, data: Any, **_: Any) -> Any:
        raise ValueError("mask failed")

    untraced = _untraced_outcome()
    with tracing_to(monkeypatch, mask=broken) as tracing:
        config = tracing.config(THREAD_ID)
        traced = _outcome(plan_scenario(planner(scripted_extractor()), config))
        text = "\n".join(
            str(v) for s in tracing.spans() for v in (s.attributes or {}).values()
        )

    assert traced == untraced
    assert "fully masked" in text
    assert "id-tron" not in text and "party_size" not in text


def test_a_handler_that_raises_does_not_break_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    untraced = _untraced_outcome()
    with tracing_to(monkeypatch) as tracing:
        raised: list[str] = []

        def explode(*_: Any, **__: Any) -> None:
            raised.append("on_chain_start")
            raise RuntimeError("handler failed")

        for handler in tracing.callbacks:
            monkeypatch.setattr(handler, "on_chain_start", explode)
        config = tracing.config(THREAD_ID)
        traced = _outcome(plan_scenario(planner(scripted_extractor()), config))

    assert raised
    assert traced == untraced
