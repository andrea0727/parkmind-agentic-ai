"""
End-to-end initial planning graph (P0-30), with every port faked: no Postgres, no
network, no MCP server.

natural language -> confirm hard constraints -> LOAD CONTEXT -> RESOLVE GROUP ->
BUILD PLAN -> CHECK -> EXPLAIN -> PROPOSE -> human approval.
"""

import re
import sys
from datetime import timedelta
from typing import Any

import factories
import pytest
from elicit_support import FakeExtractor, make_intake, make_names, scenario
from fakes import InMemorySnapshotRepository
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from planning_support import (
    NOW,
    FakeCollector,
    factory_for,
    make_deps,
    seed_snapshot,
    snapshot_context,
)

from parkmind.core.contracts import RuleId
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import approve_plan, set_constraints
from parkmind.services.clients.themeparks_errors import ThemeParksUnavailableError
from parkmind.services.ports import RoutingNotFoundError

COMPLETE = scenario("wiki_example_complete")
CONFIG: Any = {"configurable": {"thread_id": "t1"}}


def test_parkmin_state_schema_builds():
    state: ParkMindState = {"thread_id": "test_123", "iteration": 0, "messages": []}
    assert state["thread_id"] == "test_123"


def test_set_constraints_works():
    from datetime import datetime

    from parkmind.core.contracts import PARK_TZ, PartyConstraints

    state: ParkMindState = {"thread_id": "test", "messages": []}
    constraints = PartyConstraints(
        party_size=1,
        guests=[factories.guest()],
        departure_time=datetime(2026, 9, 16, 20, 0, tzinfo=PARK_TZ),
        constraints_version=1,
    )

    assert set_constraints(state, constraints)["constraints"] == constraints


def test_approve_plan_transitions_state():
    state: ParkMindState = {"thread_id": "test", "messages": [], "candidate_plan": factories.plan()}

    state = approve_plan(state)

    assert state["current_plan"] == factories.plan()
    assert state["candidate_plan"] is None
    assert state["approval"] == "APPROVED"


class _Harness:
    """The graph on in-memory ports, with every use case that matters recorded in call order."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        snapshot_age: timedelta = timedelta(0),
        collector: Any = None,
        extraction: Any = None,
    ):
        self.events: list[str] = []
        intake, sessions = make_intake()
        snapshots = InMemorySnapshotRepository()
        seed_snapshot(snapshots, snapshot_context(retrieved_at=NOW - snapshot_age - timedelta(minutes=5)))
        self.deps = make_deps(sessions=sessions, snapshots=snapshots, collector=collector)
        deps = self.deps
        self.checkpointer = default_checkpointer()
        self.graph = ipg.build_initial_planning_graph(
            FakeExtractor(extraction or COMPLETE["extraction"]),
            intake,
            self.checkpointer,
            deps_factory=factory_for(deps),
            clock=lambda: NOW,
            names=make_names(),
        )
        self._spy(monkeypatch, ipg.LoadContextUseCase, "execute", "load_context")
        self._spy(monkeypatch, ipg.BuildPlanUseCase, "resolve_group", "resolve_group")
        self._spy(monkeypatch, ipg.BuildPlanUseCase, "build", "build_plan")
        self._spy(monkeypatch, ipg.CheckPlanUseCase, "execute", "check")
        self._spy(monkeypatch, ipg.ExplainPlanUseCase, "execute", "explain")
        self._spy(monkeypatch, ipg.ProposePlanUseCase, "execute", "propose")
        self._spy_resolve(monkeypatch)

    def _spy(self, monkeypatch: pytest.MonkeyPatch, cls: Any, method: str, label: str) -> None:
        original = getattr(cls, method)

        def spy(this: Any, *args: Any, **kwargs: Any) -> Any:
            self.events.append(label)
            return original(this, *args, **kwargs)

        monkeypatch.setattr(cls, method, spy)

    def _spy_resolve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = ipg.ResolveProposalUseCase.execute

        def spy(this: Any, thread_id: str, proposal_id: str, status: Any, **kwargs: Any) -> Any:
            self.events.append(f"resolve:{status.value}")
            return original(this, thread_id, proposal_id, status, **kwargs)

        monkeypatch.setattr(ipg.ResolveProposalUseCase, "execute", spy)

    @property
    def proposals(self) -> list[Any]:
        return [p for _, p in self.deps.proposals.rows.values()]  # type: ignore[attr-defined]

    def start_to_confirmation(self) -> Any:
        result = self.graph.invoke(
            {"thread_id": "t1", "messages": [HumanMessage(content=m) for m in COMPLETE["messages"]]},
            config=CONFIG,
        )
        assert result["__interrupt__"][0].value["kind"] == "hard_constraint_confirmation"
        return result

    def confirm(self) -> Any:
        self.start_to_confirmation()
        return self.graph.invoke(Command(resume={"confirmed": True, "consent": True}), config=CONFIG)

    def state(self) -> dict[str, Any]:
        """The thread's values; while paused inside the planning subgraph, that subgraph's."""
        snapshot = self.graph.get_state(CONFIG, subgraphs=True)
        for task in snapshot.tasks:
            if task.state is not None and hasattr(task.state, "values"):
                return task.state.values
        return snapshot.values


def test_natural_language_yields_a_valid_candidate_plan_waiting_for_approval(monkeypatch):
    h = _Harness(monkeypatch)

    result = h.confirm()

    payload = result["__interrupt__"][0].value
    state = h.state()
    plan = state["candidate_plan"]
    assert payload["candidate_plan"].plan_id == plan.plan_id
    assert {"id-tron", "id-space"} <= {s.node_id for s in plan.stops}
    assert state["check_result"].valid
    assert state["explanation"]
    assert "TRON" in state["explanation"]
    assert payload["kind"] == "plan_approval"
    assert [p.explanation for p in h.proposals] == [state["explanation"]]
    assert h.deps.plans.get(plan.plan_id) == plan
    assert state["approval"] == "PENDING"
    assert state.get("current_plan") is None


def test_the_lunch_window_yields_a_meal_stop_the_routing_never_sees(monkeypatch):
    h = _Harness(monkeypatch)
    asked: list[tuple[str, str]] = []
    original = h.deps.routing.walk_minutes

    def spy(a: str, b: str) -> float:
        asked.append((a, b))
        return original(a, b)

    monkeypatch.setattr(h.deps.routing, "walk_minutes", spy)

    h.confirm()

    plan = h.state()["candidate_plan"]
    assert [s.kind.value for s in plan.stops if s.kind.value == "MEAL"] == ["MEAL"]
    assert not any("__restaurant__" in pair for pair in asked)
    assert "Lunch" in h.state()["explanation"]


def test_live_context_carries_a_complete_coverage_report(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()

    live = h.state()["live_context"]

    assert live.coverage.accessibility_checks_complete
    assert live.coverage.required_attractions_covered
    assert live.coverage.required_shows_covered
    assert live.coverage.weather_covered
    assert live.coverage.coverage_gaps == []
    assert live.accessibility_results == []


def test_stages_run_in_order_and_explain_follows_check(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()

    assert h.events == ["load_context", "resolve_group", "build_plan", "check", "explain", "propose"]


def test_the_plan_is_not_active_until_a_human_approves(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()
    pending = h.state()

    assert pending.get("current_plan") is None
    assert pending["approval"] == "PENDING"
    assert not any(e.startswith("resolve:") for e in h.events)

    result = h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)

    assert "__interrupt__" not in result
    final = h.state()
    assert final["current_plan"].plan_id == pending["candidate_plan"].plan_id
    assert final["candidate_plan"] is None
    assert final["approval"] == "APPROVED"
    assert h.events[-1] == "resolve:APPROVED"
    assert h.deps.plans.get_active("t1") == final["current_plan"]


def test_a_rejected_plan_never_becomes_current(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()

    h.graph.invoke(
        Command(resume={"decision": "REJECTED", "rejection_reason": "TOO_MUCH_WALKING"}),
        config=CONFIG,
    )

    final = h.state()
    assert final.get("current_plan") is None
    assert final["candidate_plan"] is None
    assert final["approval"] == "REJECTED"
    assert h.deps.plans.get_active("t1") is None
    assert [(p.approval_status.value, p.rejection_reason.value) for p in h.proposals] == [
        ("REJECTED", "TOO_MUCH_WALKING")
    ]


def test_a_failing_check_is_neither_explained_nor_proposed(monkeypatch):
    h = _Harness(monkeypatch, snapshot_age=timedelta(hours=3))

    result = h.confirm()

    state = h.state()
    assert "__interrupt__" not in result
    assert not state["check_result"].valid
    assert RuleId.DATA_FRESHNESS in {v.rule for v in state["check_result"].violations}
    # BUILD PLAN reloads the stale context once (the second load_context), then CHECK still fails
    assert h.events == ["load_context", "resolve_group", "build_plan", "load_context", "check"]
    assert "nothing was proposed" in state["messages"][-1].content
    assert not state.get("explanation")
    assert state.get("proposal") is None
    assert state.get("current_plan") is None


def test_nothing_is_planned_before_the_hard_constraints_are_confirmed(monkeypatch):
    h = _Harness(monkeypatch)

    h.start_to_confirmation()

    assert h.events == []
    assert h.state().get("live_context") is None


def test_it_plans_from_the_stored_snapshot_when_the_provider_is_down(monkeypatch):
    collector = FakeCollector(error=ThemeParksUnavailableError("provider down"))
    h = _Harness(monkeypatch, collector=collector)

    h.confirm()

    assert collector.calls == 1
    assert h.state()["check_result"].valid


def test_the_graph_runs_without_an_mcp_server(monkeypatch):
    before = {m for m in sys.modules if m == "mcp" or m.startswith("mcp.")}
    h = _Harness(monkeypatch)

    h.confirm()

    assert h.state()["check_result"].valid
    assert {m for m in sys.modules if m == "mcp" or m.startswith("mcp.")} == before


SENSITIVE_EXTRACTION = {
    **COMPLETE["extraction"],
    "accessibility": [
        {
            "guest_ref": 2,
            "mobility_requirements": ["WHEELCHAIR"],
            "daily_walking_limit_minutes": 173,
            "rest_frequency_minutes": 97,
        },
        {"guest_ref": 1, "ride_restrictions": ["NOT_RECOMMENDED_EXPECTANT"]},
        {"guest_ref": 3, "ride_restrictions": ["REQUIRES_TRANSFER_FROM_WHEELCHAIR"]},
    ],
}
SENSITIVE_VALUES = ["173", "97", "WHEELCHAIR", "NOT_RECOMMENDED_EXPECTANT", "REQUIRES_TRANSFER_FROM_WHEELCHAIR"]


def _everything_checkpointed(checkpointer: Any) -> str:
    """Every checkpoint and pending write of every namespace, serialized as the saver stores it."""
    chunks: list[str] = []
    for item in checkpointer.list(CONFIG):
        chunks.append(repr(item.checkpoint["channel_values"]))
        chunks.append(checkpointer.serde.dumps_typed(item.checkpoint["channel_values"])[1].decode("utf-8", "replace"))
        chunks.extend(repr(write) for write in item.pending_writes or [])
        chunks.append(repr(item.metadata))
    return "\n".join(chunks)


def test_accessibility_requirements_never_enter_graph_state(monkeypatch):
    h = _Harness(monkeypatch, extraction=SENSITIVE_EXTRACTION)
    h.confirm()
    h.graph.invoke(Command(resume={"decision": "REJECTED"}), config=CONFIG)

    state = h.state()

    assert "accessibility" not in state
    assert state["accessibility_ref"]
    assert all(isinstance(ref, str) for ref in state["accessibility_ref"])
    assert state["check_result"] is not None  # the planning stage ran, with the requirements in play
    assert state["live_context"].accessibility_results == []
    hard = state["group_objective"].hard_constraints
    assert (hard.per_guest_daily_walking_limits, hard.per_guest_rest_frequency, hard.ride_restrictions) == ({}, {}, {})

    stored = _everything_checkpointed(h.checkpointer)
    assert "accessibility_ref" in stored  # the search is looking at the real checkpoints
    for value in SENSITIVE_VALUES:
        assert not re.search(rf"(?<![\w-]){value}(?![\w-])", stored), f"{value!r} was checkpointed"


@pytest.mark.parametrize(
    "bad",
    [
        "yes",
        {"decision": "MAYBE"},
        {"decision": "EDITED"},
        {"decision": "PENDING"},
        {"decision": "REJECTED", "rejection_reason": "BECAUSE"},
    ],
)
def test_an_invalid_approval_answer_asks_again_and_changes_nothing(monkeypatch, bad):
    h = _Harness(monkeypatch)
    h.confirm()

    again = h.graph.invoke(Command(resume=bad), config=CONFIG)

    payload = again["__interrupt__"][0].value
    assert payload["kind"] == "plan_approval"
    assert payload["error"]
    assert not any(e.startswith("resolve:") for e in h.events)
    assert [p.approval_status.value for p in h.proposals] == ["PENDING"]
    assert h.deps.plans.get_active("t1") is None

    final = h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)

    assert "__interrupt__" not in final
    assert h.deps.plans.get_active("t1") is not None


def test_a_new_proposal_supersedes_the_pending_one(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()
    first = h.proposals[0]
    plan = h.state()["candidate_plan"]

    ipg.ProposePlanUseCase(factory_for(h.deps)).execute(
        "t1", plan, proposal_id="prop_second", reason="again", explanation="x", at=NOW
    )

    status = {p.proposal_id: p.approval_status.value for p in h.proposals}
    assert status == {first.proposal_id: "SUPERSEDED", "prop_second": "PENDING"}


def test_a_port_failure_ends_the_stage_with_a_message_and_no_plan(monkeypatch):
    h = _Harness(monkeypatch)

    def routing_down(*args: Any, **kwargs: Any) -> Any:
        raise RoutingNotFoundError("no route")

    monkeypatch.setattr(h.deps.routing, "walk_minutes", routing_down)

    result = h.confirm()

    state = h.state()
    assert "__interrupt__" not in result
    assert state.get("candidate_plan") is None
    assert state.get("proposal") is None
    assert state["messages"][-1].content == ipg.UNAVAILABLE_MESSAGE
    assert h.proposals == []


class _FlakyCollector:
    """Fails on the first collection, answers with ``fresh`` afterwards."""

    def __init__(self, fresh: Any) -> None:
        self._fresh = fresh
        self.calls = 0

    def collect(self, *, now: Any) -> Any:
        from parkmind.services.use_cases.collect_snapshot import CollectResult

        self.calls += 1
        if self.calls == 1:
            raise ThemeParksUnavailableError("provider down")
        return CollectResult(self._fresh.snapshot_id, created=True, live_context=self._fresh)


def test_stale_data_is_reloaded_once_and_the_plan_then_validates(monkeypatch):
    fresh = snapshot_context(snapshot_id="fresh", retrieved_at=NOW - timedelta(minutes=5))
    collector = _FlakyCollector(fresh)
    h = _Harness(monkeypatch, snapshot_age=timedelta(hours=3), collector=collector)

    result = h.confirm()

    state = h.state()
    assert collector.calls == 2
    assert state["live_context"].snapshot_id == "fresh"
    assert state["check_result"].valid
    assert state["live_context"].accessibility_results == []
    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert h.events[:5] == ["load_context", "resolve_group", "build_plan", "load_context", "check"]


def test_stale_data_that_stays_stale_ends_with_the_reason(monkeypatch):
    h = _Harness(monkeypatch, snapshot_age=timedelta(hours=3))

    h.confirm()

    state = h.state()
    message = state["messages"][-1].content
    assert "nothing was proposed" in message
    assert state["check_result"] is not None and not state["check_result"].valid
    assert state.get("candidate_plan") is None
    assert h.proposals == []
