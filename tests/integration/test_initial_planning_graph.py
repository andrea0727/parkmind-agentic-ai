"""
End-to-end initial planning graph, with every port faked: no Postgres, no
network, no MCP server.

natural language -> confirm hard constraints -> LOAD CONTEXT -> RESOLVE GROUP ->
BUILD PLAN -> CHECK -> EXPLAIN -> PROPOSE -> human approval.
"""

import json
import re
import sys
from datetime import timedelta
from typing import Any

import factories
import pytest
from elicit_support import FakeExtractor, make_intake, make_names, scenario
from explain_support import NoNarrator, ScriptedNarrator
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

from parkmind.agents.elicit_prompt import ELICIT_PROMPT_VERSION
from parkmind.core.contracts import (
    AttractionStatus,
    CheckResult,
    ConstraintViolation,
    RuleId,
)
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
    state: ParkMindState = {
        "thread_id": "test",
        "messages": [],
        "candidate_plan": factories.plan(),
    }

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
        narrator: Any = None,
    ):
        self.events: list[str] = []
        intake, sessions = make_intake()
        snapshots = InMemorySnapshotRepository()
        seed_snapshot(
            snapshots,
            snapshot_context(retrieved_at=NOW - snapshot_age - timedelta(minutes=5)),
        )
        self.deps = make_deps(
            sessions=sessions, snapshots=snapshots, collector=collector
        )
        deps = self.deps
        self.checkpointer = default_checkpointer()
        self.graph = ipg.build_initial_planning_graph(
            FakeExtractor(extraction or COMPLETE["extraction"]),
            intake,
            self.checkpointer,
            deps_factory=factory_for(deps),
            clock=lambda: NOW,
            names=make_names(),
            narrator=narrator or NoNarrator(),
        )
        self._spy(monkeypatch, ipg.LoadContextUseCase, "execute", "load_context")
        self._spy(monkeypatch, ipg.BuildPlanUseCase, "resolve_group", "resolve_group")
        self._spy(monkeypatch, ipg.BuildPlanUseCase, "build", "build_plan")
        self._spy(monkeypatch, ipg.CheckPlanUseCase, "execute", "check")
        self._spy(monkeypatch, ipg.ExplainPlanUseCase, "prepare", "explain")
        self._spy(monkeypatch, ipg.ProposePlanUseCase, "execute", "propose")
        self._spy_resolve(monkeypatch)

    def _spy(
        self, monkeypatch: pytest.MonkeyPatch, cls: Any, method: str, label: str
    ) -> None:
        original = getattr(cls, method)

        def spy(this: Any, *args: Any, **kwargs: Any) -> Any:
            self.events.append(label)
            return original(this, *args, **kwargs)

        monkeypatch.setattr(cls, method, spy)

    def _spy_resolve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = ipg.ResolveProposalUseCase.execute

        def spy(
            this: Any, thread_id: str, proposal_id: str, status: Any, **kwargs: Any
        ) -> Any:
            self.events.append(f"resolve:{status.value}")
            return original(this, thread_id, proposal_id, status, **kwargs)

        monkeypatch.setattr(ipg.ResolveProposalUseCase, "execute", spy)

    @property
    def proposals(self) -> list[Any]:
        return [p for _, p in self.deps.proposals.rows.values()]  # type: ignore[attr-defined]

    def start_to_confirmation(self) -> Any:
        result = self.graph.invoke(
            {
                "thread_id": "t1",
                "messages": [HumanMessage(content=m) for m in COMPLETE["messages"]],
            },
            config=CONFIG,
        )
        assert (
            result["__interrupt__"][0].value["kind"] == "hard_constraint_confirmation"
        )
        return result

    def confirm(self) -> Any:
        self.start_to_confirmation()
        return self.graph.invoke(
            Command(resume={"confirmed": True, "consent": True}), config=CONFIG
        )

    def state(self) -> dict[str, Any]:
        """The thread's values; while paused inside the planning subgraph, that subgraph's."""
        snapshot = self.graph.get_state(CONFIG, subgraphs=True)
        for task in snapshot.tasks:
            if task.state is not None and hasattr(task.state, "values"):
                return task.state.values
        return snapshot.values


def test_natural_language_yields_a_valid_candidate_plan_waiting_for_approval(
    monkeypatch,
):
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

    assert h.events == [
        "load_context",
        "resolve_group",
        "build_plan",
        "check",
        "explain",
        "propose",
    ]


def test_approving_a_proposal_superseded_meanwhile_activates_nothing(monkeypatch):
    """A significant event supersedes the proposal while the guests decide."""
    h = _Harness(monkeypatch)
    h.confirm()
    pending = h.state()
    h.deps.proposals.supersede_pending("t1", at=NOW)

    result = h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)

    assert "__interrupt__" not in result
    final = h.state()
    assert final.get("current_plan") is None
    assert final["candidate_plan"] is None
    assert final["proposal"] is None
    assert final["approval"] is None
    assert h.deps.plans.get_active("t1") is None
    assert final["messages"][-1].content == ipg.SUPERSEDED_MESSAGE
    superseded = h.deps.proposals.get(pending["proposal"].proposal_id)
    assert superseded.approval_status.value == "SUPERSEDED"


def test_a_superseded_proposal_says_the_current_plan_stays_only_when_there_is_one(
    monkeypatch,
):
    """Review #86 (I7)."""
    h = _Harness(monkeypatch)
    h.graph.invoke(
        {
            "thread_id": "t1",
            "messages": [HumanMessage(content=m) for m in COMPLETE["messages"]],
            "current_plan": factories.plan(plan_id="plan_in_use"),
        },
        config=CONFIG,
    )
    h.graph.invoke(Command(resume={"confirmed": True, "consent": True}), config=CONFIG)
    h.deps.proposals.supersede_pending("t1", at=NOW)

    h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)

    assert h.state()["messages"][-1].content == ipg.SUPERSEDED_KEEPS_PLAN_MESSAGE
    assert "current plan" not in ipg.SUPERSEDED_MESSAGE


def test_a_decision_already_stored_is_applied_again_not_reported_as_superseded(
    monkeypatch,
):
    """LangGraph re-runs the approval node if it stops after the decision was stored."""
    h = _Harness(monkeypatch)
    h.confirm()
    pending = h.state()
    h.deps.proposals.resolve(
        pending["proposal"].proposal_id, ipg.ApprovalStatus.APPROVED, at=NOW
    )
    h.deps.plans.activate("t1", pending["candidate_plan"].plan_id, at=NOW)

    h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)

    final = h.state()
    assert final["approval"] == "APPROVED"
    assert final["current_plan"].plan_id == pending["candidate_plan"].plan_id
    assert final["messages"][-1].content != ipg.SUPERSEDED_MESSAGE


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
        Command(
            resume={"decision": "REJECTED", "rejection_reason": "TOO_MUCH_WALKING"}
        ),
        config=CONFIG,
    )

    final = h.state()
    assert final.get("current_plan") is None
    assert final["candidate_plan"] is None
    assert final["approval"] == "REJECTED"
    assert h.deps.plans.get_active("t1") is None
    assert [
        (p.approval_status.value, p.rejection_reason.value) for p in h.proposals
    ] == [("REJECTED", "TOO_MUCH_WALKING")]


def test_a_failing_check_is_neither_explained_nor_proposed(monkeypatch):
    h = _Harness(monkeypatch, snapshot_age=timedelta(hours=3))

    result = h.confirm()

    state = h.state()
    assert "__interrupt__" not in result
    assert not state["check_result"].valid
    assert RuleId.DATA_FRESHNESS in {v.rule for v in state["check_result"].violations}
    # BUILD PLAN reloads the stale context once (the second load_context), then CHECK still fails
    assert h.events == [
        "load_context",
        "resolve_group",
        "build_plan",
        "load_context",
        "check",
    ]
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
SENSITIVE_VALUES = [
    "173",
    "97",
    "WHEELCHAIR",
    "NOT_RECOMMENDED_EXPECTANT",
    "REQUIRES_TRANSFER_FROM_WHEELCHAIR",
]


def _everything_checkpointed(checkpointer: Any) -> str:
    """Every checkpoint and pending write of every namespace, serialized as the saver stores it."""
    chunks: list[str] = []
    for item in checkpointer.list(CONFIG):
        chunks.append(repr(item.checkpoint["channel_values"]))
        chunks.append(
            checkpointer.serde.dumps_typed(item.checkpoint["channel_values"])[1].decode(
                "utf-8", "replace"
            )
        )
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
    assert (
        state["check_result"] is not None
    )  # the planning stage ran, with the requirements in play
    assert state["live_context"].accessibility_results == []
    hard = state["group_objective"].hard_constraints
    assert (
        hard.per_guest_daily_walking_limits,
        hard.per_guest_rest_frequency,
        hard.ride_restrictions,
    ) == ({}, {}, {})

    stored = _everything_checkpointed(h.checkpointer)
    assert (
        "accessibility_ref" in stored
    )  # the search is looking at the real checkpoints
    for value in SENSITIVE_VALUES:
        assert not re.search(rf"(?<![\w-]){value}(?![\w-])", stored), (
            f"{value!r} was checkpointed"
        )


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
        return CollectResult(
            self._fresh.snapshot_id, created=True, live_context=self._fresh
        )


def test_stale_data_is_reloaded_once_and_the_plan_then_validates(monkeypatch):
    fresh = snapshot_context(
        snapshot_id="fresh", retrieved_at=NOW - timedelta(minutes=5)
    )
    collector = _FlakyCollector(fresh)
    h = _Harness(monkeypatch, snapshot_age=timedelta(hours=3), collector=collector)

    result = h.confirm()

    state = h.state()
    assert collector.calls == 2
    assert state["live_context"].snapshot_id == "fresh"
    assert state["check_result"].valid
    assert state["live_context"].accessibility_results == []
    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert h.events[:5] == [
        "load_context",
        "resolve_group",
        "build_plan",
        "load_context",
        "check",
    ]


def test_stale_data_that_stays_stale_ends_with_the_reason(monkeypatch):
    h = _Harness(monkeypatch, snapshot_age=timedelta(hours=3))
    h.confirm()

    state = h.state()
    message = state["messages"][-1].content
    assert "nothing was proposed" in message
    assert state["check_result"] is not None and not state["check_result"].valid
    assert state.get("candidate_plan") is None
    assert h.proposals == []


def _edit_of(plan: Any) -> dict[str, list[str]]:
    """Drop an optional attraction (the fixture plan already has the whole catalog)."""
    stops = [s.node_id for s in plan.stops if s.kind.value == "ATTRACTION"]
    optional = [n for n in stops if n not in ("id-tron", "id-space")]
    assert optional, "the fixture plan leaves nothing to remove"
    return {"remove": optional[:1], "add": []}


def test_an_edit_is_checked_again_and_proposed_as_a_new_pending_proposal(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()
    first = h.state()
    edit = _edit_of(first["candidate_plan"])
    h.events.clear()

    result = h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": edit}), config=CONFIG
    )

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert h.events == [
        "resolve:EDITED",
        "load_context",
        "resolve_group",
        "build_plan",
        "check",
        "explain",
        "propose",
    ]
    edited = h.state()
    plan = edited["candidate_plan"]
    assert plan.plan_id != first["candidate_plan"].plan_id
    assert edited["check_result"].valid
    assert not {s.node_id for s in plan.stops} & set(edit["remove"])
    assert edited["constraints"].avoid == edit["remove"]
    assert (
        edited["constraints"].constraints_version
        == first["constraints"].constraints_version + 1
    )
    assert edited["approval"] == "PENDING"
    # Proposed: nothing of the edit is left to fall back on later.
    assert edited.get("plan_before_edit") is None
    assert edited.get("constraints_before_edit") is None
    status = {p.proposal_id: p.approval_status.value for p in h.proposals}
    assert status == {
        first["proposal"].proposal_id: "EDITED",
        edited["proposal"].proposal_id: "PENDING",
    }
    assert h.deps.plans.get_active("t1") is None


def test_an_attraction_added_back_is_planned_as_a_must_do(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()
    removed = _edit_of(h.state()["candidate_plan"])["remove"]
    h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": {"remove": removed}}),
        config=CONFIG,
    )

    h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": {"add": removed}}), config=CONFIG
    )

    edited = h.state()
    assert set(removed) <= {s.node_id for s in edited["candidate_plan"].stops}
    assert set(removed) <= set(edited["constraints"].must_do)
    assert edited["constraints"].avoid == []
    assert edited["check_result"].valid


def test_an_edited_plan_becomes_active_only_once_approved(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()
    h.graph.invoke(
        Command(
            resume={"decision": "EDITED", "edit": _edit_of(h.state()["candidate_plan"])}
        ),
        config=CONFIG,
    )
    edited = h.state()

    h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)

    assert h.deps.plans.get_active("t1").plan_id == edited["candidate_plan"].plan_id


def test_every_decision_is_in_the_behavior_log_of_each_guest(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()
    first = h.state()
    edit = _edit_of(first["candidate_plan"])
    h.graph.invoke(Command(resume={"decision": "EDITED", "edit": edit}), config=CONFIG)
    h.graph.invoke(
        Command(resume={"decision": "REJECTED", "rejection_reason": "BAD_TIMING"}),
        config=CONFIG,
    )

    for guest in first["constraints"].guests:
        logged = {
            e.decision: e for e in h.deps.behavior.get(f"t1:{guest.guest_id}").entries
        }
        assert set(logged) == {"EDITED", "REJECTED"}
        assert logged["EDITED"].proposal_id == first["proposal"].proposal_id
        assert logged["EDITED"].attraction_ids == [
            s.node_id
            for s in first["candidate_plan"].stops
            if s.kind.value in ("ATTRACTION", "SHOW")
        ]
        assert logged["EDITED"].removed_attraction_ids == edit["remove"]
        assert logged["EDITED"].added_attraction_ids == edit["add"]
        assert logged["REJECTED"].removed_attraction_ids == []
        assert logged["REJECTED"].added_attraction_ids == []
        assert logged["REJECTED"].proposal_id != first["proposal"].proposal_id
        assert logged["REJECTED"].rejection_reason.value == "BAD_TIMING"


@pytest.mark.parametrize(
    "edit",
    [
        {},
        {"remove": ["id-not-in-the-plan"]},
        {"add": ["id-unknown"]},
        {"add": ["id-tron"]},
        {"remove": ["id-tron"], "add": ["id-tron"]},
        {"remove": [], "add": [], "stops": []},
    ],
)
def test_an_edit_that_does_not_fit_asks_again_and_changes_nothing(monkeypatch, edit):
    h = _Harness(monkeypatch)
    h.confirm()
    pending = h.state()

    again = h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": edit}), config=CONFIG
    )

    payload = again["__interrupt__"][0].value
    assert payload["kind"] == "plan_approval"
    assert payload["error"]
    assert not any(e.startswith("resolve:") for e in h.events)
    assert [p.approval_status.value for p in h.proposals] == ["PENDING"]
    assert h.state()["constraints"] == pending["constraints"]
    assert h.deps.guests.list_all() == []


# --- EXPLAIN through the grounded narrator ------------------------------------


def test_a_grounded_narration_is_what_the_guests_read(monkeypatch):
    narrator = ScriptedNarrator("Here is your day at the park.")
    h = _Harness(monkeypatch, narrator=narrator)

    h.confirm()

    [proposal] = h.proposals
    assert proposal.explanation == "Here is your day at the park."
    assert proposal.provenance.prompt_versions["explain"] == "explain-v1"
    assert proposal.provenance.prompt_versions["elicit"] == ELICIT_PROMPT_VERSION
    payload = json.loads(narrator.payloads[0])
    assert payload["stops"] and "preferences" in payload


def test_an_ungrounded_narration_falls_back_to_the_template(monkeypatch):
    narrator = ScriptedNarrator("A 999 minute wait.", "Still a 999 minute wait.")
    h = _Harness(monkeypatch, narrator=narrator)

    h.confirm()

    [proposal] = h.proposals
    assert "999" not in proposal.explanation
    assert re.match(r"\d+ stops, ", proposal.explanation)
    assert proposal.provenance.prompt_versions["explain"] == "template"


def _fail_plans_without(monkeypatch: pytest.MonkeyPatch, node_id: str) -> None:
    """The checker refuses any plan that lacks ``node_id`` (an edit that cannot fit)."""
    original = ipg.CheckPlanUseCase.execute

    def check(this: Any, **kwargs: Any) -> Any:
        result = original(this, **kwargs)
        if node_id in {s.node_id for s in kwargs["plan"].stops}:
            return result
        violation = ConstraintViolation(
            rule=RuleId.MUST_DO, message="the edit does not fit"
        )
        return CheckResult(valid=False, violations=[violation])

    monkeypatch.setattr(ipg.CheckPlanUseCase, "execute", check)


def test_an_edit_that_cannot_be_planned_gives_back_the_plan_before_it(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()
    first = h.state()
    edit = _edit_of(first["candidate_plan"])
    _fail_plans_without(monkeypatch, edit["remove"][0])
    h.events.clear()

    result = h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": edit}), config=CONFIG
    )

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert h.events == [
        "resolve:EDITED",
        "load_context",
        "resolve_group",
        "build_plan",
        "check",
        "check",
        "explain",
        "propose",
    ]
    offered = h.state()
    # The same plan, under a new id (a stored plan body never changes).
    assert offered["candidate_plan"].plan_id != first["candidate_plan"].plan_id
    assert offered["candidate_plan"].stops == first["candidate_plan"].stops
    assert offered["constraints"] == first["constraints"]
    assert offered["check_result"].valid
    assert offered.get("plan_before_edit") is None
    assert offered.get("constraints_before_edit") is None
    status = {p.proposal_id: p.approval_status.value for p in h.proposals}
    assert status == {
        first["proposal"].proposal_id: "EDITED",
        offered["proposal"].proposal_id: "PENDING",
    }
    said = [m.content for m in offered["messages"]]
    assert any(
        ipg.EDIT_DID_NOT_FIT_MESSAGE in m and "the edit does not fit" in m for m in said
    )

    h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)

    assert h.deps.plans.get_active("t1").plan_id == offered["candidate_plan"].plan_id


def test_when_the_plan_before_the_edit_no_longer_passes_nothing_is_proposed(
    monkeypatch,
):
    h = _Harness(monkeypatch)
    h.confirm()
    edit = _edit_of(h.state()["candidate_plan"])

    def refuse(this: Any, **kwargs: Any) -> Any:
        violation = ConstraintViolation(rule=RuleId.MUST_DO, message="nothing fits")
        return CheckResult(valid=False, violations=[violation])

    monkeypatch.setattr(ipg.CheckPlanUseCase, "execute", refuse)

    result = h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": edit}), config=CONFIG
    )

    assert "__interrupt__" not in result
    final = h.state()
    assert final["candidate_plan"] is None
    assert final.get("plan_before_edit") is None
    assert [p.approval_status.value for p in h.proposals] == ["EDITED"]
    assert h.deps.plans.get_active("t1") is None


def test_a_closed_attraction_added_is_planned_as_an_unmet_must_do(monkeypatch):
    """Adding a ride that is down is not a failed edit: it is named as not fitting today."""
    h = _Harness(monkeypatch)
    h.confirm()
    removed = _edit_of(h.state()["candidate_plan"])["remove"]
    h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": {"remove": removed}}),
        config=CONFIG,
    )
    live = snapshot_context(
        snapshot_id="snap_down", retrieved_at=NOW - timedelta(minutes=1)
    )
    down = {node_id: AttractionStatus.DOWN for node_id in removed}
    seed_snapshot(
        h.deps.snapshots,
        live.model_copy(update={"statuses": {**live.statuses, **down}}),
    )

    h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": {"add": removed}}), config=CONFIG
    )

    edited = h.state()
    assert edited["check_result"].valid
    assert edited["candidate_plan"].unmet_must_do == removed
    assert edited["approval"] == "PENDING"


def test_a_second_edit_builds_on_the_first(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()
    first = h.state()
    optional = [
        s.node_id
        for s in first["candidate_plan"].stops
        if s.kind.value == "ATTRACTION" and s.node_id not in ("id-tron", "id-space")
    ]
    h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": {"remove": optional[:1]}}),
        config=CONFIG,
    )
    second = h.state()

    h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": {"remove": optional[1:2]}}),
        config=CONFIG,
    )

    third = h.state()
    assert third["constraints"].avoid == optional[:2]
    assert (
        third["constraints"].constraints_version
        == first["constraints"].constraints_version + 2
    )
    guest = first["constraints"].guests[0].guest_id
    ids = sorted(e.entry_id for e in h.deps.behavior.get(f"t1:{guest}").entries)
    assert ids == sorted(f"{p['proposal'].proposal_id}:EDITED" for p in (first, second))


def test_a_refused_edit_can_be_followed_by_a_valid_one(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()
    edit = _edit_of(h.state()["candidate_plan"])
    h.graph.invoke(
        Command(
            resume={"decision": "EDITED", "edit": {"remove": ["id-not-in-the-plan"]}}
        ),
        config=CONFIG,
    )

    result = h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": edit}), config=CONFIG
    )

    assert "error" not in result["__interrupt__"][0].value
    assert h.state()["constraints"].avoid == edit["remove"]
    assert [p.approval_status.value for p in h.proposals] == ["EDITED", "PENDING"]


def test_missing_park_data_fails_the_run_and_a_retry_applies_the_stored_answer(
    monkeypatch,
):
    """Replay-safe: an outage is never recorded as a refused answer. The run raises,
    LangGraph keeps the answer, and retrying the run (no new input) applies it.

    The answer is keyed by its interrupt id: inside the planning subgraph a plain
    resume value kept from a failed run would also answer the next interrupt.
    """
    h = _Harness(monkeypatch)
    pending = h.confirm()["__interrupt__"][0]
    edit = _edit_of(h.state()["candidate_plan"])
    original = ipg.ApplyEditUseCase.execute
    calls = {"n": 0}

    def flaky(this: Any, *args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ipg.PlanningUnavailableError("catalog unavailable")
        return original(this, *args, **kwargs)

    monkeypatch.setattr(ipg.ApplyEditUseCase, "execute", flaky)

    answer = {pending.id: {"decision": "EDITED", "edit": edit}}
    with pytest.raises(ipg.PlanningUnavailableError):
        h.graph.invoke(Command(resume=answer), config=CONFIG)
    assert [p.approval_status.value for p in h.proposals] == ["PENDING"]

    result = h.graph.invoke(None, config=CONFIG)

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert "error" not in result["__interrupt__"][0].value
    assert h.state()["constraints"].avoid == edit["remove"]
    assert [p.approval_status.value for p in h.proposals] == ["EDITED", "PENDING"]


@pytest.mark.parametrize("decision", ["APPROVED", "REJECTED"])
def test_an_edit_sent_with_another_decision_is_refused(monkeypatch, decision):
    h = _Harness(monkeypatch)
    h.confirm()

    again = h.graph.invoke(
        Command(resume={"decision": decision, "edit": {"remove": ["id-tron"]}}),
        config=CONFIG,
    )

    assert "EDITED" in again["__interrupt__"][0].value["error"]
    assert [p.approval_status.value for p in h.proposals] == ["PENDING"]


def test_a_rejection_reason_is_ignored_on_approval(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()

    result = h.graph.invoke(
        Command(resume={"decision": "APPROVED", "rejection_reason": "BECAUSE"}),
        config=CONFIG,
    )

    assert "__interrupt__" not in result
    assert h.deps.plans.get_active("t1") is not None


def test_the_plan_is_re_offered_even_when_its_new_explanation_is_the_template(
    monkeypatch,
):
    """The re-offer is explained again; a different prompt version must not clash with
    the plan stored under the first one (plan bodies are immutable)."""
    narrator = ScriptedNarrator(
        "Here is your day at the park.", "A 999 minute wait.", "Still 999."
    )
    h = _Harness(monkeypatch, narrator=narrator)
    h.confirm()
    first = h.state()
    assert first["candidate_plan"].provenance.prompt_versions["explain"] == "explain-v1"
    edit = _edit_of(first["candidate_plan"])
    _fail_plans_without(monkeypatch, edit["remove"][0])

    result = h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": edit}), config=CONFIG
    )

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    offered = h.state()
    assert offered["candidate_plan"].provenance.prompt_versions["explain"] == "template"
    assert [p.approval_status.value for p in h.proposals] == ["EDITED", "PENDING"]


def test_an_open_attraction_that_no_longer_fits_fails_the_real_check_and_re_offers(
    monkeypatch,
):
    """No fake checker: a ride added back while its wait fills the rest of the day."""
    h = _Harness(monkeypatch)
    h.confirm()
    removed = _edit_of(h.state()["candidate_plan"])["remove"]
    h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": {"remove": removed}}),
        config=CONFIG,
    )
    before = h.state()
    live = snapshot_context(
        snapshot_id="snap_queue", retrieved_at=NOW - timedelta(minutes=1)
    )
    waits = {
        node_id: live.waits[node_id].model_copy(update={"wait_minutes": 900.0})
        for node_id in removed
    }
    seed_snapshot(
        h.deps.snapshots, live.model_copy(update={"waits": {**live.waits, **waits}})
    )
    h.events.clear()

    result = h.graph.invoke(
        Command(resume={"decision": "EDITED", "edit": {"add": removed}}), config=CONFIG
    )

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert (
        h.events.count("check") == 2
    )  # the edited plan fails, the one before it passes
    offered = h.state()
    assert offered["constraints"] == before["constraints"]
    assert offered["candidate_plan"].stops == before["candidate_plan"].stops
    assert any(
        ipg.EDIT_DID_NOT_FIT_MESSAGE in str(m.content) for m in offered["messages"]
    )
    assert [p.approval_status.value for p in h.proposals] == [
        "EDITED",
        "EDITED",
        "PENDING",
    ]


def test_nothing_is_proposed_for_a_party_without_guests(monkeypatch):
    saved: list[Any] = []
    monkeypatch.setattr(
        ipg.ProposePlanUseCase, "execute", lambda *a, **k: saved.append(a)
    )
    state: Any = {
        "thread_id": "t1",
        "messages": [],
        "candidate_plan": factories.plan(),
        "check_result": CheckResult(valid=True),
        "explanation": "x",
        "constraints": factories.party_constraints(guests=[]),
    }

    with pytest.raises(ValueError, match="party's guests"):
        ipg._propose_plan(state)

    assert saved == []


def test_the_plan_records_which_elicit_prompt_read_the_constraints(monkeypatch):
    """Prompts are versioned and the version flows into Provenance (.claude/rules/agents.md)."""
    h = _Harness(monkeypatch)

    h.confirm()

    [proposal] = h.proposals
    assert proposal.provenance.prompt_versions["elicit"] == ipg.ELICIT_PROMPT_VERSION
    assert (
        h.state()["candidate_plan"].provenance.prompt_versions["elicit"]
        == ipg.ELICIT_PROMPT_VERSION
    )


def _fail_check_with(
    monkeypatch: pytest.MonkeyPatch, rule: RuleId, message: str
) -> None:
    result = CheckResult(
        valid=False, violations=[ConstraintViolation(rule=rule, message=message)]
    )
    monkeypatch.setattr(ipg.CheckPlanUseCase, "execute", lambda *a, **k: result)


def test_a_failed_show_arrival_says_the_show_could_not_be_fitted_not_that_shows_cannot_be_planned(
    monkeypatch,
):
    _fail_check_with(
        monkeypatch,
        RuleId.SHOW_ARRIVAL,
        "No scheduled showtime for id-parade within the stop window",
    )
    h = _Harness(monkeypatch)

    result = h.confirm()

    message = h.state()["messages"][-1].content
    assert "__interrupt__" not in result
    assert "No scheduled showtime for id-parade" in message
    assert "couldn't fit a show at its showtime" in message
    assert "can't be planned" not in message
    assert h.state().get("proposal") is None


def test_the_show_explanation_is_only_given_for_show_violations(monkeypatch):
    _fail_check_with(monkeypatch, RuleId.MUST_DO, "nothing fits")
    h = _Harness(monkeypatch)

    h.confirm()

    assert "couldn't fit a show" not in h.state()["messages"][-1].content


def _only_children() -> dict[str, Any]:
    extraction = dict(COMPLETE["extraction"])
    extraction["guests"] = [{"role": "child"} for _ in extraction["guests"]]
    extraction["accessibility"] = []
    return extraction


def test_a_plan_for_a_group_without_an_adult_cannot_be_approved(monkeypatch):
    h = _Harness(monkeypatch, extraction=_only_children())
    h.confirm()

    again = h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)

    payload = again["__interrupt__"][0].value
    assert payload["kind"] == "plan_approval"
    assert "no adult" in payload["error"]
    assert not any(e.startswith("resolve:") for e in h.events)
    assert [p.approval_status.value for p in h.proposals] == ["PENDING"]
    assert h.deps.plans.get_active("t1") is None


def test_a_group_without_an_adult_can_still_reject_its_plan(monkeypatch):
    h = _Harness(monkeypatch, extraction=_only_children())
    h.confirm()

    h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)
    h.graph.invoke(
        Command(resume={"decision": "REJECTED", "rejection_reason": "OTHER"}),
        config=CONFIG,
    )

    assert h.state()["approval"] == "REJECTED"
    assert h.deps.plans.get_active("t1") is None


def test_a_group_with_an_adult_is_not_blocked_from_approving(monkeypatch):
    h = _Harness(monkeypatch)
    h.confirm()

    final = h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)

    assert "__interrupt__" not in final
    assert h.deps.plans.get_active("t1") is not None
