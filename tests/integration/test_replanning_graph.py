"""
Replanning graph end to end: approved plan -> event -> policy -> replan -> check ->
diff -> explain -> propose -> interrupt -> approve / reject.

Real use cases, planner, checker and in-memory ports; only the clock is fixed. The thread is
first brought to an approved active plan by the initial planning graph, then the replanning
graph runs on the same thread with the new events as input.
"""

import itertools
import json
from datetime import timedelta
from typing import Any

import factories
import pytest
from explain_support import NoNarrator, ScriptedNarrator
from langgraph.types import Command
from planning_support import NOW, factory_for
from test_initial_planning_graph import CONFIG, _Harness

from parkmind.agents import elicit_agent, elicit_prompt
from parkmind.core.contracts import (
    ApprovalStatus,
    ConstraintViolation,
    EventSource,
    EventType,
    RejectionReason,
    RuleId,
    StopKind,
)
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph import replanning_graph as rg
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import discarded_proposal_update
from parkmind.services.use_cases.planning_deps import PlanningUnavailableError


class _Replanning:
    """An approved plan on thread t1 plus the replanning graph over the same ports."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        approve: bool = True,
        narrator: Any = None,
    ) -> None:
        self.h = _Harness(monkeypatch)
        self.h.confirm()
        if approve:
            self.h.graph.invoke(Command(resume={"decision": "APPROVED"}), config=CONFIG)
        self.graph = rg.build_replanning_graph(
            self.h.checkpointer,
            deps_factory=factory_for(self.h.deps),
            clock=lambda: NOW,
            narrator=narrator or NoNarrator(),
        )

    @property
    def deps(self) -> Any:
        return self.h.deps

    @property
    def active(self) -> Any:
        return self.deps.plans.get_active("t1")

    @property
    def proposals(self) -> list[Any]:
        return self.h.proposals

    def pending(self) -> list[Any]:
        return [
            p for p in self.proposals if p.approval_status is ApprovalStatus.PENDING
        ]

    def send(self, *events: Any) -> Any:
        return self.graph.invoke(
            {"thread_id": "t1", "events": list(events)}, config=CONFIG
        )

    def resume(self, value: Any) -> Any:
        return self.graph.invoke(Command(resume=value), config=CONFIG)

    def values(self) -> dict[str, Any]:
        return self.graph.get_state(CONFIG).values

    @property
    def guest_id(self) -> str:
        return str(self.values()["constraints"].guests[0].guest_id)

    def stop_node(self, index: int = 0) -> str:
        """A stop of the active plan that is still ahead of the party."""
        return self.active.stops[index].node_id


def _down(node_id: str, event_id: str = "ev_down", minutes: int = 1) -> Any:
    return factories.event(
        event_id=event_id,
        type=EventType.ATTRACTION_DOWN,
        attraction_id=node_id,
        timestamp=NOW + timedelta(minutes=minutes),
    )


def _recorded(
    r: "_Replanning", event_type: EventType, attraction_id: str | None = None
) -> list[Any]:
    """The thread's stored events of a type: ids are rewritten to the cross-source id."""
    return [
        e
        for e in r.deps.events.list_for_thread("t1")
        if e.type is event_type
        and (attraction_id is None or e.attraction_id == attraction_id)
    ]


def _interrupt(result: Any) -> Any:
    assert "__interrupt__" in result, "the graph should be waiting for a human"
    return result["__interrupt__"][0].value


def _last_message(result: Any) -> str:
    return str(result["messages"][-1].content)


@pytest.fixture
def r(monkeypatch: pytest.MonkeyPatch) -> _Replanning:
    return _Replanning(monkeypatch)


# --- a significant event creates a proposal -------------------------------------------------


def test_a_significant_event_creates_a_pending_proposal_waiting_for_approval(r) -> None:
    before = r.active
    result = r.send(_down(r.stop_node()))

    payload = _interrupt(result)
    assert payload["kind"] == "plan_approval"
    [proposal] = r.pending()
    assert proposal.base_plan_id == before.plan_id
    assert (
        proposal.candidate_plan_id != before.plan_id
    )  # a replan is a new plan_id [C23]
    [recorded] = _recorded(r, EventType.ATTRACTION_DOWN, r.stop_node())
    assert proposal.triggering_event_id == recorded.event_id
    assert proposal.reason
    assert proposal.diff is not None
    assert proposal.explanation


def test_the_replanned_plan_avoids_the_closed_attraction(r) -> None:
    closed = r.stop_node()
    r.send(_down(closed))

    candidate = r.deps.plans.get(r.pending()[0].candidate_plan_id)
    assert closed not in [
        s.node_id for s in candidate.stops if s.kind is StopKind.ATTRACTION
    ]


def test_the_proposal_changes_nothing_until_a_human_decides(r) -> None:
    before = r.active
    r.send(_down(r.stop_node()))

    assert r.active == before
    assert r.deps.plans.get_active("t1").plan_id == before.plan_id


# --- insignificant events are ignored -----------------------------------------------------------


def test_an_insignificant_event_is_ignored(r) -> None:
    before = r.active
    unrelated = factories.event(
        event_id="ev_low",
        type=EventType.ATTRACTION_DOWN,
        attraction_id="id-not-in-plan-and-not-a-must-do",
        timestamp=NOW + timedelta(minutes=1),
    )
    result = r.send(unrelated)

    assert "__interrupt__" not in result
    assert r.proposals and all(
        p.approval_status is ApprovalStatus.APPROVED for p in r.proposals
    )
    assert r.active == before
    [recorded] = _recorded(
        r, EventType.ATTRACTION_DOWN, "id-not-in-plan-and-not-a-must-do"
    )
    assert recorded.requires_replan is False


def test_party_relocation_never_triggers_a_replan(r) -> None:
    before = r.active
    moved = factories.event(
        event_id="ev_move",
        type=EventType.PARTY_RELOCATED,
        source=EventSource.USER,
        attraction_id=None,
        location_node_id=r.stop_node(),
        timestamp=NOW + timedelta(minutes=1),
    )
    result = r.send(moved)

    assert "__interrupt__" not in result
    assert r.active == before
    assert len(r.proposals) == 1


def test_a_duplicate_event_does_not_replan_again(r) -> None:
    event = _down(r.stop_node())
    r.send(event)
    r.resume({"decision": "REJECTED", "rejection_reason": "TOO_MUCH_WALKING"})
    proposals = len(r.proposals)

    result = r.send(event)

    assert "__interrupt__" not in result
    assert len(r.proposals) == proposals


# --- the same constraint gate as the initial plan ---------------------------------------------------


def test_the_proposed_replan_passes_the_same_checker_as_an_initial_plan(r) -> None:
    r.send(_down(r.stop_node()))

    values = r.values()
    assert values["check_result"].valid is True
    assert values["candidate_plan"].plan_id == r.pending()[0].candidate_plan_id


def test_a_replan_that_fails_the_gate_is_never_proposed(r, monkeypatch) -> None:
    original = rg.CheckPlanUseCase.execute_replan

    def reject(this: Any, **kwargs: Any) -> Any:
        result = original(this, **kwargs)
        return result.model_copy(
            update={
                "valid": False,
                "violations": [
                    ConstraintViolation(
                        rule=RuleId.DATA_FRESHNESS, message="stale data"
                    )
                ],
            }
        )

    monkeypatch.setattr(rg.CheckPlanUseCase, "execute_replan", reject)
    before = r.active
    result = r.send(_down(r.stop_node()))

    assert "__interrupt__" not in result
    assert r.pending() == []
    assert r.active == before
    assert "stays as it was" in _last_message(result)
    assert "stale data" in _last_message(result)
    assert r.values().get("candidate_plan") is None


def test_proposing_requires_a_checked_candidate(r) -> None:
    state: ParkMindState = {"thread_id": "t1"}  # type: ignore[typeddict-item]
    with pytest.raises(ValueError):
        rg._propose_replan(state, deps_factory=factory_for(r.deps), clock=lambda: NOW)


# --- human approval ------------------------------------------------------------------------------


def test_approving_makes_the_replan_the_active_plan(r) -> None:
    before = r.active
    r.send(_down(r.stop_node()))
    candidate_id = r.pending()[0].candidate_plan_id

    result = r.resume({"decision": "APPROVED"})

    assert "__interrupt__" not in result
    assert r.active.plan_id == candidate_id != before.plan_id
    assert r.pending() == []
    assert r.values()["current_plan"].plan_id == candidate_id
    assert r.values()["approval"] == "APPROVED"


def test_approving_keeps_the_reported_progress_for_the_new_plan(r) -> None:
    done = r.active.stops[0].node_id
    r.deps.execution_states.save(
        factories.execution_state(
            plan_id=r.active.plan_id, as_of=NOW, completed_stop_ids=[done]
        )
    )
    r.send(_down(r.stop_node(1)))
    candidate_id = r.pending()[0].candidate_plan_id

    r.resume({"decision": "APPROVED"})

    saved = r.deps.execution_states.get(candidate_id)
    assert saved is not None
    assert (saved.plan_id, saved.completed_stop_ids) == (candidate_id, [done])


def test_progress_reported_while_the_proposal_waits_is_kept_on_approval(r) -> None:
    first, second = r.active.stops[0].node_id, r.active.stops[1].node_id
    r.deps.execution_states.save(
        factories.execution_state(
            plan_id=r.active.plan_id, as_of=NOW, completed_stop_ids=[first]
        )
    )
    r.send(_down(r.stop_node(2)))
    candidate_id = r.pending()[0].candidate_plan_id
    assert r.deps.execution_states.get(candidate_id) is None
    r.deps.execution_states.save(
        factories.execution_state(
            plan_id=r.active.plan_id,
            as_of=NOW + timedelta(minutes=5),
            completed_stop_ids=[first, second],
        )
    )

    r.resume({"decision": "APPROVED"})

    saved = r.deps.execution_states.get(candidate_id)
    assert saved is not None and saved.completed_stop_ids == [first, second]


def test_progress_nobody_reported_is_not_stored_for_the_new_plan(r) -> None:
    r.send(_down(r.stop_node()))
    candidate_id = r.pending()[0].candidate_plan_id

    r.resume({"decision": "APPROVED"})

    assert r.deps.execution_states.get(candidate_id) is None


def test_rejecting_leaves_the_active_plan_unchanged(r) -> None:
    before = r.active
    r.send(_down(r.stop_node()))

    result = r.resume({"decision": "REJECTED", "rejection_reason": "TOO_MUCH_WALKING"})

    assert "__interrupt__" not in result
    assert r.active == before
    assert r.deps.plans.get_active("t1").plan_id == before.plan_id
    rejected = [p for p in r.proposals if p.approval_status is ApprovalStatus.REJECTED]
    assert [p.rejection_reason for p in rejected] == [RejectionReason.TOO_MUCH_WALKING]
    assert r.pending() == []
    assert r.values()["current_plan"].plan_id == before.plan_id
    assert r.values().get("candidate_plan") is None


def test_an_invalid_approval_answer_asks_again_and_changes_nothing(r) -> None:
    before = r.active
    r.send(_down(r.stop_node()))

    result = r.resume({"decision": "MAYBE"})

    assert _interrupt(result)["kind"] == "plan_approval"
    assert r.active == before
    assert len(r.pending()) == 1


def test_the_llm_has_no_path_to_activation(r) -> None:
    """Nothing but the human-resumed interrupt ever changes the active plan."""
    before = r.active
    r.send(_down(r.stop_node()))
    r.send(_down(r.stop_node(1), event_id="ev_down_2", minutes=2))

    assert r.active.plan_id == before.plan_id


# --- superseding ------------------------------------------------------------------------------


def test_a_new_significant_event_supersedes_the_pending_proposal(r) -> None:
    first_stop, second_stop = r.stop_node(0), r.stop_node(1)
    r.send(_down(first_stop))
    [first] = r.pending()

    result = r.send(_down(second_stop, event_id="ev_down_2", minutes=2))

    assert _interrupt(result)["kind"] == "plan_approval"
    statuses = {p.proposal_id: p.approval_status for p in r.proposals}
    assert statuses[first.proposal_id] is ApprovalStatus.SUPERSEDED
    [second] = r.pending()
    assert second.proposal_id != first.proposal_id
    [second_event] = _recorded(r, EventType.ATTRACTION_DOWN, second_stop)
    assert second.triggering_event_id == second_event.event_id
    assert (
        second.base_plan_id == r.active.plan_id
    )  # still the plan in force, not the first candidate


def test_approving_after_supersession_activates_only_the_newest(r) -> None:
    r.send(_down(r.stop_node(0)))
    stale = r.pending()[0]
    r.send(_down(r.stop_node(1), event_id="ev_down_2", minutes=2))
    newest = r.pending()[0]

    r.resume({"decision": "APPROVED"})

    assert r.active.plan_id == newest.candidate_plan_id
    assert r.active.plan_id != stale.candidate_plan_id


def test_an_insignificant_event_leaves_the_pending_proposal_open_for_a_decision(
    r,
) -> None:
    r.send(_down(r.stop_node()))
    [pending] = r.pending()
    noise = factories.event(
        event_id="ev_noise",
        type=EventType.ATTRACTION_DOWN,
        attraction_id="id-not-in-plan-and-not-a-must-do",
        timestamp=NOW + timedelta(minutes=2),
    )

    result = r.send(noise)

    # Not superseded; the same proposal is put back in front of the guests.
    assert [p.proposal_id for p in r.pending()] == [pending.proposal_id]
    payload = _interrupt(result)
    assert payload["kind"] == "plan_approval"
    r.resume({"decision": "APPROVED"})
    assert r.active.plan_id == pending.candidate_plan_id


def test_a_reopened_proposal_can_be_rejected(r) -> None:
    before = r.active
    r.send(_down(r.stop_node()))
    noise = factories.event(
        event_id="ev_noise",
        type=EventType.ATTRACTION_DOWN,
        attraction_id="id-not-in-plan-and-not-a-must-do",
        timestamp=NOW + timedelta(minutes=2),
    )
    r.send(noise)

    r.resume({"decision": "REJECTED", "rejection_reason": "OTHER"})

    assert r.active == before
    assert r.pending() == []


def _unsure_fatigue(r: "_Replanning") -> Any:
    return factories.event(
        event_id="ev_unsure",
        type=EventType.GUEST_FATIGUE,
        source=EventSource.USER,
        attraction_id=None,
        guest_id=r.guest_id,
        confidence=0.4,
        timestamp=NOW + timedelta(minutes=1),
    )


# --- fatigue -> rest first ---------------------------------------------------------------------


def test_confirmed_fatigue_proposes_a_plan_that_opens_with_a_rest(r) -> None:
    result = r.send(_unsure_fatigue(r))
    assert _interrupt(result)["kind"] == rg.EVENT_CONFIRMATION_INTERRUPT_KIND
    result = r.resume({"confirmed": True})

    assert _interrupt(result)["kind"] == "plan_approval"
    candidate = r.deps.plans.get(r.pending()[0].candidate_plan_id)
    horizon = [s for s in candidate.stops if s.arrival_time >= NOW]
    assert horizon[0].kind is StopKind.REST
    assert r.active.plan_id != candidate.plan_id


def test_a_rest_first_replan_passes_its_check_while_the_clock_keeps_advancing(
    r,
) -> None:
    """The check reads the progress report's time, not the clock: a clock that moves on
    between BUILD and CHECK must not make the rest-first plan start in the past."""
    ticks = iter(NOW + timedelta(minutes=2 * i) for i in itertools.count())
    r.graph = rg.build_replanning_graph(
        r.h.checkpointer,
        deps_factory=factory_for(r.deps),
        clock=lambda: next(ticks),
        narrator=NoNarrator(),
    )

    result = r.send(_unsure_fatigue(r))
    assert _interrupt(result)["kind"] == rg.EVENT_CONFIRMATION_INTERRUPT_KIND
    result = r.resume({"confirmed": True})

    assert _interrupt(result)["kind"] == "plan_approval"
    candidate = r.deps.plans.get(r.pending()[0].candidate_plan_id)
    first_ahead = next(s for s in candidate.stops if s.arrival_time >= NOW)
    assert first_ahead.kind is StopKind.REST


# --- event confirmation --------------------------------------------------------------------------


def test_a_doubtful_user_report_asks_the_guests_before_anything_is_recorded(r) -> None:
    result = r.send(_unsure_fatigue(r))

    payload = _interrupt(result)
    assert payload["kind"] == rg.EVENT_CONFIRMATION_INTERRUPT_KIND
    assert [e.type for e in payload["events"]] == [EventType.GUEST_FATIGUE]
    assert _recorded(r, EventType.GUEST_FATIGUE) == []
    assert r.pending() == []


def test_a_confirmed_report_is_recorded_and_replans(r) -> None:
    r.send(_unsure_fatigue(r))

    result = r.resume({"confirmed": True})

    assert len(_recorded(r, EventType.GUEST_FATIGUE)) == 1
    assert _interrupt(result)["kind"] == "plan_approval"
    assert len(r.pending()) == 1


def test_a_bare_boolean_confirms_too(r) -> None:
    r.send(_unsure_fatigue(r))

    result = r.resume(True)

    assert _interrupt(result)["kind"] == "plan_approval"


def test_a_dismissed_report_leaves_no_trace(r) -> None:
    before = r.active
    r.send(_unsure_fatigue(r))

    result = r.resume({"confirmed": False})

    assert "__interrupt__" not in result
    assert _recorded(r, EventType.GUEST_FATIGUE) == []
    assert r.pending() == []
    assert r.active == before
    assert r.values()["event_confirmation"] == "DISMISSED"


@pytest.mark.parametrize(
    "bad",
    ["yes", 1, {"confirmed": "yes"}, {"confirmed": None}, {"decision": "APPROVED"}],
)
def test_an_invalid_confirmation_asks_again_and_records_nothing(r, bad) -> None:
    r.send(_unsure_fatigue(r))

    result = r.resume(bad)

    payload = _interrupt(result)
    assert payload["kind"] == rg.EVENT_CONFIRMATION_INTERRUPT_KIND
    assert "error" in payload
    assert _recorded(r, EventType.GUEST_FATIGUE) == []


def test_a_closure_is_not_held_back_by_a_doubtful_report(r) -> None:
    result = r.send(_down(r.stop_node()), _unsure_fatigue(r))

    # The monitor's closure goes ahead of the question: it is recorded already.
    payload = _interrupt(result)
    assert payload["kind"] == rg.EVENT_CONFIRMATION_INTERRUPT_KIND
    assert len(_recorded(r, EventType.ATTRACTION_DOWN)) == 1


# --- nothing to replan / failures ---------------------------------------------------------------


def test_with_no_active_plan_there_is_nothing_to_replan(monkeypatch) -> None:
    r = _Replanning(monkeypatch, approve=False)

    result = r.send(_down("id-tron"))

    assert "__interrupt__" not in result
    assert _last_message(result) == rg.NOTHING_TO_REPLAN_MESSAGE
    assert r.deps.events.list_for_thread("t1") == []


def test_a_thread_that_never_planned_has_nothing_to_replan(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    graph = rg.build_replanning_graph(
        h.checkpointer,
        deps_factory=factory_for(h.deps),
        clock=lambda: NOW,
        narrator=NoNarrator(),
    )

    result = graph.invoke(
        {"thread_id": "t9", "events": [_down("id-tron")]},
        config={"configurable": {"thread_id": "t9"}},
    )

    assert _last_message(result) == rg.NOTHING_TO_REPLAN_MESSAGE


def test_an_unavailable_port_ends_with_a_message_and_keeps_the_plan(
    r, monkeypatch
) -> None:
    before = r.active

    def down(*_a: Any, **_k: Any) -> Any:
        raise PlanningUnavailableError("provider down")

    monkeypatch.setattr(rg.ReplanUseCase, "execute", down)
    result = r.send(_down(r.stop_node()))

    assert "__interrupt__" not in result
    assert _last_message(result) == ipg.UNAVAILABLE_MESSAGE
    assert r.pending() == []
    assert r.active == before


def test_a_replan_with_no_valid_result_says_so_and_proposes_nothing(
    r, monkeypatch
) -> None:
    from parkmind.services.planning.replanner import ReplanResult

    original = rg.ReplanUseCase.execute

    def fail(this: Any, request: Any) -> Any:
        outcome = original(this, request)
        return type(outcome)(
            ReplanResult(
                valid=False,
                plan=None,
                diff=None,
                unmet_must_do=["id-tron"],
                fatal_error="no fit",
            ),
            outcome.live_context,
            outcome.execution_state,
        )

    monkeypatch.setattr(rg.ReplanUseCase, "execute", fail)
    before = r.active
    result = r.send(_down(r.stop_node()))

    message = _last_message(result)
    assert "nothing was proposed" in message
    assert "no fit" in message
    assert "id-tron" in message
    assert "stays as it was" in message
    assert r.pending() == []
    assert r.active == before


def test_a_failed_replan_supersedes_the_old_proposal_and_clears_it_from_state(
    r, monkeypatch
) -> None:
    r.send(_down(r.stop_node(0)))
    [first] = r.pending()
    from parkmind.services.planning.replanner import ReplanResult

    original = rg.ReplanUseCase.execute

    def fail(this: Any, request: Any) -> Any:
        outcome = original(this, request)
        return type(outcome)(
            ReplanResult(
                valid=False,
                plan=None,
                diff=None,
                unmet_must_do=[],
                fatal_error="no fit",
            ),
            outcome.live_context,
            outcome.execution_state,
        )

    monkeypatch.setattr(rg.ReplanUseCase, "execute", fail)
    r.send(_down(r.stop_node(1), event_id="ev_down_2", minutes=2))

    statuses = {p.proposal_id: p.approval_status for p in r.proposals}
    assert statuses[first.proposal_id] is ApprovalStatus.SUPERSEDED
    values = r.values()
    for key, value in discarded_proposal_update().items():
        assert values.get(key) == value


# --- privacy and checkpoints -------------------------------------------------------------------


def test_accessibility_requirements_never_enter_replanning_state(r) -> None:
    from test_initial_planning_graph import _everything_checkpointed

    r.send(_down(r.stop_node()))

    blob = _everything_checkpointed(r.h.checkpointer)
    assert "daily_walking_limit_minutes" not in blob


def test_the_state_tells_the_guests_what_changed(r) -> None:
    r.send(_down(r.stop_node()))

    values = r.values()
    assert values["diff"] is not None
    assert values["explanation"]
    assert r.stop_node() in values["diff"].stops_removed


# --- editing a replan ------------------------------------------------------------------


def _optional_stop(plan: Any, *, besides: tuple[str, ...] = ()) -> str:
    """An attraction of the plan that is not a must-do."""
    return next(
        s.node_id
        for s in plan.stops
        if s.kind is StopKind.ATTRACTION
        and s.node_id not in ("id-tron", "id-space", *besides)
    )


def test_an_edited_replan_is_checked_again_and_proposed_against_the_active_plan(
    r,
) -> None:
    before = r.active
    r.send(_down(r.stop_node()))
    [first] = r.pending()
    dropped = _optional_stop(r.values()["candidate_plan"])

    result = r.resume({"decision": "EDITED", "edit": {"remove": [dropped]}})

    assert _interrupt(result)["kind"] == "plan_approval"
    [second] = r.pending()
    assert second.proposal_id != first.proposal_id
    assert second.base_plan_id == before.plan_id
    edited = r.values()
    assert edited["check_result"].valid
    assert edited.get("plan_before_edit") is None
    assert edited.get("constraints_before_edit") is None
    assert dropped not in {s.node_id for s in edited["candidate_plan"].stops}
    assert dropped in second.diff.stops_removed
    status = {p.proposal_id: p.approval_status for p in r.proposals}
    assert status[first.proposal_id] is ApprovalStatus.EDITED
    assert r.active == before

    r.resume({"decision": "APPROVED"})

    assert r.active.plan_id == second.candidate_plan_id


def test_a_reopened_proposal_can_be_edited(r) -> None:
    """The reopened proposal carries the context of the run that proposed it; an edit
    plans on freshly loaded data instead."""
    r.send(_down(r.stop_node()))
    noise = factories.event(
        event_id="ev_noise",
        type=EventType.ATTRACTION_DOWN,
        attraction_id="id-not-in-plan-and-not-a-must-do",
        timestamp=NOW + timedelta(minutes=2),
    )
    r.send(noise)
    dropped = _optional_stop(r.values()["candidate_plan"])
    r.h.events.clear()

    result = r.resume({"decision": "EDITED", "edit": {"remove": [dropped]}})

    assert _interrupt(result)["kind"] == "plan_approval"
    assert r.h.events[:3] == ["resolve:EDITED", "load_context", "resolve_group"]
    assert dropped not in {s.node_id for s in r.values()["candidate_plan"].stops}


def test_a_stop_already_done_cannot_be_edited_out(r) -> None:
    done = r.active.stops[0].node_id
    r.deps.execution_states.save(
        factories.execution_state(
            plan_id=r.active.plan_id, as_of=NOW, completed_stop_ids=[done]
        )
    )
    r.send(_down(r.stop_node(1)))
    [pending] = r.pending()

    result = r.resume({"decision": "EDITED", "edit": {"remove": [done]}})

    assert _interrupt(result)["error"]
    assert [p.proposal_id for p in r.pending()] == [pending.proposal_id]


def test_the_replan_narrator_explains_from_the_reason_and_the_changes(
    monkeypatch,
) -> None:
    """An updated plan is explained from why it changed and what changed."""
    narrator = ScriptedNarrator("Your plan changed because a ride closed.")
    r = _Replanning(monkeypatch, narrator=narrator)

    r.send(_down(r.stop_node()))

    [proposal] = r.pending()
    assert proposal.explanation == "Your plan changed because a ride closed."
    payload = json.loads(narrator.payloads[0])
    assert payload["reason"] and payload["changes"] is not None


def test_an_edited_replan_that_cannot_be_planned_gives_back_the_replan_before_it(
    r, monkeypatch
) -> None:
    before = r.active
    r.send(_down(r.stop_node()))
    [first] = r.pending()
    original = r.values()["candidate_plan"]
    constraints = r.values()["constraints"]
    dropped = _optional_stop(original)
    check = rg.CheckPlanUseCase.execute_replan

    def refuse_without_dropped(this: Any, **kwargs: Any) -> Any:
        result = check(this, **kwargs)
        if dropped in {s.node_id for s in kwargs["plan"].stops}:
            return result
        violation = ConstraintViolation(
            rule=RuleId.MUST_DO, message="the edit does not fit"
        )
        return result.model_copy(update={"valid": False, "violations": [violation]})

    monkeypatch.setattr(rg.CheckPlanUseCase, "execute_replan", refuse_without_dropped)

    result = r.resume({"decision": "EDITED", "edit": {"remove": [dropped]}})

    assert _interrupt(result)["kind"] == "plan_approval"
    [second] = r.pending()
    assert second.proposal_id != first.proposal_id
    assert second.candidate_plan_id != original.plan_id
    assert r.deps.plans.get(second.candidate_plan_id).stops == original.stops
    offered = r.values()
    assert offered["constraints"] == constraints
    assert offered.get("plan_before_edit") is None
    said = [str(m.content) for m in offered["messages"]]
    assert any(
        ipg.EDIT_DID_NOT_FIT_MESSAGE in m and "the edit does not fit" in m for m in said
    )
    assert r.active == before


def test_a_replan_is_re_offered_even_when_its_new_explanation_is_the_template(
    monkeypatch,
) -> None:
    narrator = ScriptedNarrator(
        "Your plan changed because a ride closed.", "A 999 wait.", "999."
    )
    r = _Replanning(monkeypatch, narrator=narrator)
    r.send(_down(r.stop_node()))
    original = r.values()["candidate_plan"]
    assert original.provenance.prompt_versions["explain"] == "explain-v4"
    dropped = _optional_stop(original)
    check = rg.CheckPlanUseCase.execute_replan

    def refuse_without_dropped(this: Any, **kwargs: Any) -> Any:
        result = check(this, **kwargs)
        if dropped in {s.node_id for s in kwargs["plan"].stops}:
            return result
        violation = ConstraintViolation(
            rule=RuleId.MUST_DO, message="the edit does not fit"
        )
        return result.model_copy(update={"valid": False, "violations": [violation]})

    monkeypatch.setattr(rg.CheckPlanUseCase, "execute_replan", refuse_without_dropped)

    result = r.resume({"decision": "EDITED", "edit": {"remove": [dropped]}})

    assert _interrupt(result)["kind"] == "plan_approval"
    [second] = r.pending()
    assert (
        r.values()["candidate_plan"].provenance.prompt_versions["explain"] == "template"
    )
    assert r.deps.plans.get(second.candidate_plan_id).stops == original.stops


def test_nothing_is_proposed_for_a_party_without_guests(r) -> None:
    r.send(_down(r.stop_node()))
    state = {
        **r.values(),
        "constraints": r.values()["constraints"].model_copy(update={"guests": []}),
    }
    before = len(r.proposals)

    with pytest.raises(ValueError, match="party's guests"):
        rg._propose_replan(state, deps_factory=factory_for(r.deps), clock=lambda: NOW)

    assert len(r.proposals) == before


def test_a_replan_keeps_the_elicit_prompt_version_of_the_plan_it_replaces(r) -> None:
    r.send(_down(r.stop_node()))

    [proposal] = r.pending()
    assert proposal.provenance.prompt_versions["elicit"] == ipg.ELICIT_PROMPT_VERSION


def test_a_replan_after_a_new_elicit_prompt_keeps_the_version_that_read_the_constraints(
    r, monkeypatch
) -> None:
    """The constraints were read by the prompt of the plan being replaced, not by
    whichever prompt is deployed when the replan runs."""
    original = ipg.ELICIT_PROMPT_VERSION
    # Both names: the graph's import and the constant's home, wherever a stamp reads it.
    monkeypatch.setattr(ipg, "ELICIT_PROMPT_VERSION", "elicit-next")
    monkeypatch.setattr(elicit_agent, "ELICIT_PROMPT_VERSION", "elicit-next")
    monkeypatch.setattr(elicit_prompt, "ELICIT_PROMPT_VERSION", "elicit-next")

    r.send(_down(r.stop_node()))

    [proposal] = r.pending()
    assert proposal.provenance.prompt_versions["elicit"] == original
