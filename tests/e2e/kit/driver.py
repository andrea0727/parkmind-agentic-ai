"""Runs one scenario through the real graphs and returns the facts the judges read.

The same driver serves the live layer (real ports, real LLM) and the workflow layer (in-memory
ports, canned extraction): only the ``Runtime`` differs.
"""

import signal
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from e2e.kit.facts import (
    graph_state,
    interrupt_view,
    jsonable,
    plan_facts,
    scrub,
)
from e2e.kit.recorder import RecordingExtractor, StageSpy
from e2e.model import Facts, FollowUp, Scenario
from parkmind.core.contracts import Event, EventSource, EventType, StopKind


class ScenarioTimeoutError(BaseException):
    """The scenario used up its total time (LLM and database calls included).

    A ``BaseException`` so that no ``except Exception`` along the way can swallow it.
    """


@contextmanager
def time_limit(seconds: float | None) -> Iterator[None]:
    """Raise ``ScenarioTimeoutError`` in the main thread once ``seconds`` have passed."""
    if seconds is None:
        yield
        return

    def expired(signum: int, frame: Any) -> None:
        raise ScenarioTimeoutError(f"the scenario did not finish in {seconds:g}s")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@dataclass
class Built:
    graph: Any
    recorder: RecordingExtractor
    replanning: Callable[[], Any] | None = None


class Runtime(Protocol):
    def now(self) -> datetime: ...

    def build(self, scenario: Scenario) -> Built: ...

    def context(self, scenario: Scenario) -> AbstractContextManager[Any]: ...

    def rows(self, scenario: Scenario) -> int | None: ...


def _extracted(recorder: RecordingExtractor) -> dict[str, Any] | None:
    answered = [call for call in recorder.calls if call["args"] is not None]
    if not answered:
        return None
    args = answered[-1]["args"] or {}
    summary = {
        key: args.get(key)
        for key in ("party_size", "must_do", "avoid", "departure_time", "lunch_window")
    }
    guests = args.get("guests", [])
    summary["heights"] = [guest.get("height_cm") for guest in guests]
    summary["n_guests"] = len(guests)
    return summary


def _attraction_stops(plan: Any) -> list[Any]:
    return [stop for stop in plan.stops if stop.kind is StopKind.ATTRACTION]


def _follow_up(
    follow_up: FollowUp, built: Built, config: Any, now: datetime, out: Facts
) -> None:
    """An approved plan loses an attraction: the replanning graph must propose a valid detour."""
    state = graph_state(built.graph, config)
    plan = state.get("current_plan")
    stops = _attraction_stops(plan) if plan is not None else []
    if len(stops) <= follow_up.stop_index or built.replanning is None:
        out["replan"] = {"sent": False}
        return
    closed = str(stops[follow_up.stop_index].node_id)
    replanning = built.replanning()
    event = Event(
        event_id=f"ev-{uuid.uuid4().hex[:6]}",
        type=EventType.ATTRACTION_DOWN,
        source=EventSource.MONITOR,
        attraction_id=closed,
        confidence=1.0,
        timestamp=now + timedelta(minutes=1),
    )
    result = replanning.invoke(
        {"thread_id": config["configurable"]["thread_id"], "events": [event]},
        config=config,
    )
    before = replanning.get_state(config).values
    interrupt = interrupt_view(result)
    candidate = before.get("candidate_plan")
    check = before.get("check_result")
    replan: Facts = {
        "sent": True,
        "closed": closed,
        "interrupt": interrupt,
        "proposed": candidate is not None and interrupt is not None,
        "candidate_avoids_closed": candidate is not None
        and closed not in [str(s.node_id) for s in _attraction_stops(candidate)],
        "candidate_n_stops": len(candidate.stops) if candidate is not None else None,
        "check_valid": check.valid if check is not None else None,
        "active_unchanged": before.get("current_plan") is not None
        and before["current_plan"].plan_id == plan.plan_id,
        "last_ai": str(result["messages"][-1].content)
        if result.get("messages")
        else None,
    }
    if interrupt and interrupt["kind"] == "plan_approval":
        replanning.invoke(Command(resume=dict(follow_up.decision)), config=config)
        after = replanning.get_state(config).values
        current = after.get("current_plan")
        replan["new_plan_active"] = (
            current is not None
            and candidate is not None
            and current.plan_id == candidate.plan_id
        )
        replan["closed_in_active_plan"] = current is not None and closed in [
            str(s.node_id) for s in _attraction_stops(current)
        ]
    out["replan"] = replan


def run_scenario(
    scenario: Scenario, runtime: Runtime, *, timeout: float | None = None
) -> Facts:
    thread = f"{scenario.id}-{uuid.uuid4().hex[:6]}"
    now = runtime.now()
    out: Facts = {
        "id": scenario.id,
        "messages": list(scenario.texts()),
        "confirm_payload": jsonable(scenario.confirm),
        "approval_payload": jsonable(scenario.approval),
        "setup": scenario.fault.value if scenario.fault else None,
        "now": now.strftime("%H:%M"),
        "steps": [],
    }
    built = runtime.build(scenario)
    graph = built.graph
    config: Any = {"configurable": {"thread_id": thread}}
    rows_before = runtime.rows(scenario)
    started = time.time()
    with StageSpy() as spy:
        try:
            with runtime.context(scenario), time_limit(timeout):
                result = graph.invoke(
                    {
                        "thread_id": thread,
                        "messages": [HumanMessage(content=m) for m in scenario.texts()],
                    },
                    config=config,
                )
                out["llm_calls"] = len(built.recorder.calls)
                out["llm_attempts"] = jsonable(built.recorder.attempts())
                extracted = _extracted(built.recorder)
                if extracted is not None:
                    out["extracted"] = extracted
                out["stages_elicit"] = spy.take()
                first = interrupt_view(result)
                out["interrupt1"] = first
                out["facts1"] = plan_facts(graph_state(graph, config))
                if first and first["kind"] == "hard_constraint_confirmation":
                    result = graph.invoke(
                        Command(resume=scenario.confirm), config=config
                    )
                    out["stages"] = spy.take()
                    second = interrupt_view(result)
                    out["interrupt2"] = second
                    out["facts2"] = plan_facts(graph_state(graph, config))
                    if second and second["kind"] == "plan_approval":
                        result = graph.invoke(
                            Command(resume=scenario.approval), config=config
                        )
                        out["stages_after_approval"] = spy.take()
                        state = graph_state(graph, config)
                        out["approval"] = jsonable(state.get("approval"))
                        out["plan_active"] = state.get("current_plan") is not None
                        out["interrupt3"] = interrupt_view(result)
                        if scenario.follow_up is not None and out["plan_active"]:
                            _follow_up(scenario.follow_up, built, config, now, out)
        except (Exception, ScenarioTimeoutError) as exc:  # noqa: BLE001
            out["exception"] = {
                "type": type(exc).__name__,
                "message": scrub(str(exc))[:500],
            }
            out["llm_calls"] = len(built.recorder.calls)
            out["llm_attempts"] = jsonable(built.recorder.attempts())
    if rows_before is not None:
        out["attraction_rows"] = {
            "before": rows_before,
            "after": runtime.rows(scenario),
        }
    out["seconds"] = round(time.time() - started, 1)
    return out
