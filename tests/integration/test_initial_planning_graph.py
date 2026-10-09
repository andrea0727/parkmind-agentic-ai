"""
End-to-end initial planning graph (P0-30), with every port faked: no Postgres, no
network, no MCP server.

natural language -> confirm hard constraints -> LOAD CONTEXT -> RESOLVE GROUP ->
BUILD PLAN -> CHECK -> EXPLAIN -> PROPOSE -> human approval.
"""

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

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, snapshot_age: timedelta = timedelta(0), collector: Any = None):
        self.events: list[str] = []
        self.resolved: list[Any] = []
        intake, sessions = make_intake()
        snapshots = InMemorySnapshotRepository()
        seed_snapshot(snapshots, snapshot_context(retrieved_at=NOW - snapshot_age - timedelta(minutes=5)))
        deps = make_deps(sessions=sessions, snapshots=snapshots, collector=collector)
        self.graph = ipg.build_initial_planning_graph(
            FakeExtractor(COMPLETE["extraction"]),
            intake,
            default_checkpointer(),
            deps_factory=factory_for(deps),
            clock=lambda: NOW,
            names=make_names(),
        )
        self._spy(monkeypatch, ipg.LoadContextUseCase, "execute", "load_context")
        self._spy(monkeypatch, ipg.BuildPlanUseCase, "resolve_group", "resolve_group")
        self._spy(monkeypatch, ipg.BuildPlanUseCase, "build", "build_plan")
        self._spy(monkeypatch, ipg.CheckPlanUseCase, "execute", "check")
        self._spy(monkeypatch, ipg.ExplainPlanUseCase, "execute", "explain")
        monkeypatch.setattr(ipg.ProposePlanUseCase, "execute", self._propose)
        monkeypatch.setattr(ipg.ResolveProposalUseCase, "execute", self._resolve)

    def _spy(self, monkeypatch: pytest.MonkeyPatch, cls: Any, method: str, label: str) -> None:
        original = getattr(cls, method)

        def spy(this: Any, *args: Any, **kwargs: Any) -> Any:
            self.events.append(label)
            return original(this, *args, **kwargs)

        monkeypatch.setattr(cls, method, spy)

    def _propose(self, thread_id: str, plan: Any, **kwargs: Any) -> Any:
        self.events.append("propose")
        self.explanation = kwargs["explanation"]
        return factories.proposal(candidate_plan_id=plan.plan_id)

    def _resolve(self, thread_id: str, proposal_id: str, status: Any, *, at: Any, rejection_reason: Any = None) -> Any:
        self.events.append(f"resolve:{status.value}")
        self.resolved.append(status)
        return factories.proposal(proposal_id=proposal_id, approval_status=status)

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
    assert h.explanation == state["explanation"]
    assert state["approval"] == "PENDING"
    assert state.get("current_plan") is None


def test_live_context_carries_a_complete_coverage_report(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()

    live = h.state()["live_context"]

    assert live.coverage.accessibility_checks_complete
    assert live.coverage.required_attractions_covered
    assert live.coverage.required_shows_covered
    assert live.coverage.weather_covered
    assert live.coverage.coverage_gaps == []
    assert {r.guest_id for r in live.accessibility_results} == set(h.state()["accessibility_ref"])
    assert len(live.accessibility_results) == len(h.state()["accessibility_ref"]) * 7


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


def test_a_failing_check_is_neither_explained_nor_proposed(monkeypatch):
    h = _Harness(monkeypatch, snapshot_age=timedelta(hours=3))

    result = h.confirm()

    state = h.state()
    assert "__interrupt__" not in result
    assert not state["check_result"].valid
    assert RuleId.DATA_FRESHNESS in {v.rule for v in state["check_result"].violations}
    assert h.events == ["load_context", "resolve_group", "build_plan", "check"]
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


def test_accessibility_requirements_never_enter_graph_state(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()

    state = h.state()

    assert "accessibility" not in state
    assert state["accessibility_ref"]
    assert all(isinstance(ref, str) for ref in state["accessibility_ref"])
    assert "LIMITED_WALKING" not in repr({k: v for k, v in state.items() if k != "constraints"})
