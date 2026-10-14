"""Replanning building blocks: progress, reported availability, reasons, diff, proposals."""

from datetime import datetime, timedelta

import factories
import pytest
from planning_support import PlanningAttractionRepository, factory_for, make_deps

from parkmind.core.contracts import (
    PARK_TZ,
    ApprovalStatus,
    AttractionStatus,
    CheckResult,
    ConstraintViolation,
    EventSeverity,
    EventSource,
    EventType,
    PlanDiff,
    RuleId,
)
from parkmind.graph.state_helpers import active_plan_update, discarded_proposal_update
from parkmind.services.use_cases.active_plan import ActivePlanUseCase
from parkmind.services.use_cases.diff_plan import DiffPlanUseCase
from parkmind.services.use_cases.explain_plan import (
    ExplainBeforeValidationError,
    ExplainPlanUseCase,
    explain_replan,
)
from parkmind.services.use_cases.pending_proposal import PendingProposalUseCase
from parkmind.services.use_cases.propose_plan import ProposePlanUseCase
from parkmind.services.use_cases.replan import (
    ReplanRequest,
    check_progress_of_plan,
    effective_execution_state,
    progress_source,
    with_event_statuses,
)
from parkmind.services.use_cases.replan_reason import (
    ReplanReasonUseCase,
    replan_reason,
)

NOW = datetime(2026, 9, 16, 10, 30, tzinfo=PARK_TZ)
THREAD = "t1"
NAMES = {"a1": "Space Mountain", "a2": "Jungle Cruise", "a3": "Pirates"}


def _stop(node_id: str, start: int, minutes: int = 30, **kw):
    arrival = NOW + timedelta(minutes=start)
    return factories.stop(
        node_id=node_id,
        arrival_time=arrival,
        departure_time=arrival + timedelta(minutes=minutes),
        **kw,
    )


def _plan(plan_id: str = "plan_1", *stops):
    return factories.plan(
        plan_id=plan_id,
        stops=list(stops) or [_stop("a1", -90), _stop("a2", -30), _stop("a3", 30)],
    )


def _event(event_type=EventType.ATTRACTION_DOWN, minutes=0, **kw):
    fields = {
        "event_id": f"ev-{event_type.value}-{minutes}-{kw.get('attraction_id', 'a1')}",
        "type": event_type,
        "timestamp": NOW + timedelta(minutes=minutes),
        "requires_replan": True,
        "severity": EventSeverity.HIGH,
    }
    return factories.event(**{**fields, **kw})


# --- effective_execution_state ------------------------------------------------------------------


def test_untracked_progress_assumes_the_party_is_on_schedule():
    plan = _plan()

    state = effective_execution_state(plan, None, NOW)

    # a1 ended at -60 min, a2 at 0 min (departure <= now); a3 is still ahead.
    assert state.completed_stop_ids == ["a1", "a2"]
    assert state.plan_id == plan.plan_id
    assert state.as_of == NOW


def test_reported_progress_is_trusted_and_not_overridden_by_the_schedule():
    stored = factories.execution_state(
        plan_id="plan_1", completed_stop_ids=["a1"], as_of=NOW - timedelta(minutes=5)
    )

    state = effective_execution_state(_plan(), stored, NOW)

    assert state.completed_stop_ids == ["a1"]
    assert state.walking_minutes_consumed == {"g1": 12.0}
    assert state.remaining_walking_cap_minutes == {"g1": 78.0}


def test_skipped_stops_count_as_reported_progress():
    stored = factories.execution_state(
        plan_id="plan_1", completed_stop_ids=[], skipped_stop_ids=["a1"], as_of=NOW
    )

    assert effective_execution_state(_plan(), stored, NOW).completed_stop_ids == []


def test_as_of_never_lies_before_now():
    stale = factories.execution_state(plan_id="plan_1", as_of=NOW - timedelta(hours=1))
    ahead = factories.execution_state(plan_id="plan_1", as_of=NOW + timedelta(minutes=10))

    assert effective_execution_state(_plan(), stale, NOW).as_of == NOW
    assert effective_execution_state(_plan(), ahead, NOW).as_of == NOW + timedelta(minutes=10)


def test_the_progress_label_follows_where_the_completed_stops_come_from():
    reported = factories.execution_state(plan_id="plan_1", completed_stop_ids=["a1"])
    skipped = factories.execution_state(
        plan_id="plan_1", completed_stop_ids=[], skipped_stop_ids=["a1"]
    )
    located = factories.execution_state(
        plan_id="plan_1", completed_stop_ids=[], current_location_node_id="a2"
    )

    assert progress_source(reported, sent=True) == "sent"
    assert progress_source(reported, sent=False) == "stored"
    assert progress_source(skipped, sent=True) == "sent"
    assert progress_source(None, sent=False) == "assumed"
    # sent with a location and walking but no stops: the stops done are the schedule's
    assert progress_source(located, sent=True) == "assumed"
    assert effective_execution_state(_plan(), located, NOW).completed_stop_ids == [
        "a1",
        "a2",
    ]


def test_progress_must_be_for_the_plan_and_only_about_its_stops():
    plan = _plan()
    stray = factories.execution_state(plan_id="plan_1", completed_stop_ids=["ghost"])
    other = factories.execution_state(plan_id="another", completed_stop_ids=["a1"])
    fine = factories.execution_state(plan_id="plan_1", completed_stop_ids=["a1"])

    check_progress_of_plan(plan, fine)
    with pytest.raises(ValueError, match="stops of the plan"):
        check_progress_of_plan(plan, stray)
    with pytest.raises(ValueError, match="execution_state is for plan"):
        check_progress_of_plan(plan, other)


def test_a_request_refuses_progress_about_stops_the_plan_does_not_have():
    stray = factories.execution_state(plan_id="plan_1", completed_stop_ids=["ghost"])

    with pytest.raises(ValueError, match="stops of the plan"):
        ReplanRequest(
            thread_id=THREAD,
            plan=_plan(),
            constraints=factories.party_constraints(),
            profiles=[],
            accessibility_ref=[],
            live_context=_context(),
            objective=object(),
            now=NOW,
            execution_state=stray,
        )


def test_the_stored_state_is_not_mutated():
    stored = factories.execution_state(plan_id="plan_1", completed_stop_ids=["a1"])
    before = stored.model_copy(deep=True)

    effective_execution_state(_plan(), stored, NOW + timedelta(hours=1))

    assert stored == before


# --- with_event_statuses --------------------------------------------------------------------------


def _context(**statuses):
    return factories.live_context(
        retrieved_at=NOW,
        statuses={"a1": AttractionStatus.OPERATING, "a2": AttractionStatus.OPERATING, **statuses},
    )


def test_a_report_newer_than_the_snapshot_marks_the_attraction_down():
    context = _context()

    updated = with_event_statuses(context, [_event(minutes=1, attraction_id="a2")])

    assert updated.statuses["a2"] is AttractionStatus.DOWN
    assert updated.statuses["a1"] is AttractionStatus.OPERATING
    assert context.statuses["a2"] is AttractionStatus.OPERATING  # the input is not mutated


def test_a_report_not_newer_than_the_snapshot_is_ignored():
    context = _context()

    same = with_event_statuses(context, [_event(minutes=0, attraction_id="a2")])
    older = with_event_statuses(context, [_event(minutes=-5, attraction_id="a2")])

    assert same is context and older is context  # unchanged context is returned as is


def test_the_latest_report_wins():
    context = _context()
    events = [
        _event(EventType.ATTRACTION_AVAILABLE, minutes=3, attraction_id="a2"),
        _event(EventType.ATTRACTION_DOWN, minutes=1, attraction_id="a2"),
    ]

    assert with_event_statuses(context, events).statuses["a2"] is AttractionStatus.OPERATING
    assert with_event_statuses(context, events[::-1]).statuses["a2"] is AttractionStatus.OPERATING


def test_an_attraction_reported_available_again_clears_a_closure_in_the_snapshot():
    context = _context(a2=AttractionStatus.DOWN)

    updated = with_event_statuses(
        context, [_event(EventType.ATTRACTION_AVAILABLE, minutes=2, attraction_id="a2")]
    )

    assert updated.statuses["a2"] is AttractionStatus.OPERATING


def test_a_reopening_stamped_before_the_snapshot_wins_when_the_caller_sent_it():
    """``newer_only=False``: what the client sends overrides the snapshot's DOWN, as its
    closure overrides an OPERATING one, whatever the timestamp."""
    context = _context(a2=AttractionStatus.DOWN)
    reopened = _event(EventType.ATTRACTION_AVAILABLE, minutes=-5, attraction_id="a2")

    assert with_event_statuses(context, [reopened]) is context
    updated = with_event_statuses(context, [reopened], newer_only=False)

    assert updated.statuses["a2"] is AttractionStatus.OPERATING


@pytest.mark.parametrize(
    "event",
    [
        _event(EventType.WAIT_SPIKE, minutes=1, attraction_id="a2", delta_minutes=30),
        _event(EventType.RAIN_INCOMING, minutes=1, attraction_id=None, affected_hours=[NOW]),
        _event(EventType.BEHIND_SCHEDULE, minutes=1, attraction_id=None, minutes_behind=20),
    ],
)
def test_other_event_types_do_not_touch_availability(event):
    context = _context()

    assert with_event_statuses(context, [event]) is context


# --- replan_reason ---------------------------------------------------------------------------------


def test_the_reason_names_the_attraction_not_the_event_type():
    reason = replan_reason([_event(attraction_id="a1")], NAMES)

    assert reason.text == "Updated plan because Space Mountain is down."
    assert "ATTRACTION_DOWN" not in reason.text


def test_the_most_severe_event_leads_and_is_the_trigger():
    low = _event(EventType.WAIT_SPIKE, minutes=1, attraction_id="a2", delta_minutes=25,
                 severity=EventSeverity.LOW)
    high = _event(minutes=5, attraction_id="a1", severity=EventSeverity.HIGH)

    reason = replan_reason([low, high], NAMES)

    assert reason.triggering_event_id == high.event_id
    assert reason.text.index("Space Mountain") < reason.text.index("Jungle Cruise")
    assert reason.text == (
        "Updated plan because Space Mountain is down and the wait at Jungle Cruise jumped by 25 min."
    )


def test_ties_break_by_time_then_id():
    first = _event(minutes=1, attraction_id="a1")
    second = _event(minutes=2, attraction_id="a2")

    assert replan_reason([second, first], NAMES).triggering_event_id == first.event_id


def test_events_that_did_not_require_a_replan_are_not_blamed():
    quiet = _event(minutes=1, attraction_id="a2", requires_replan=False)

    reason = replan_reason([quiet, _event(attraction_id="a1")], NAMES)

    assert "Jungle Cruise" not in reason.text


def test_the_same_cause_is_said_once():
    twice = [_event(minutes=1, attraction_id="a1"), _event(minutes=2, attraction_id="a1")]

    assert replan_reason(twice, NAMES).text == "Updated plan because Space Mountain is down."


def test_without_a_trigger_the_reason_is_generic_and_has_no_event():
    reason = replan_reason([_event(requires_replan=False)], NAMES)

    assert reason.triggering_event_id is None
    assert reason.text
    assert replan_reason([], NAMES) == reason


def test_an_unknown_attraction_falls_back_to_its_id():
    assert "zz9" in replan_reason([_event(attraction_id="zz9")], NAMES).text


@pytest.mark.parametrize(
    ("event", "phrase"),
    [
        (_event(EventType.ATTRACTION_AVAILABLE, attraction_id="a1"), "Space Mountain is available again"),
        (_event(EventType.WAIT_SPIKE, attraction_id="a1", delta_minutes=30), "the wait at Space Mountain jumped by 30 min"),
        (_event(EventType.RAIN_INCOMING, attraction_id=None, affected_hours=[NOW]), "rain is coming"),
        (_event(EventType.BEHIND_SCHEDULE, attraction_id=None, minutes_behind=15), "the party is 15 min behind schedule"),
        (_event(EventType.GUEST_FATIGUE, attraction_id=None, guest_id="g1", source=EventSource.USER), "a guest is tired"),
    ],
)
def test_each_event_type_has_a_plain_phrase(event, phrase):
    assert phrase in replan_reason([event], NAMES).text


def test_the_use_case_resolves_names_from_the_catalog():
    deps = make_deps(
        attractions=PlanningAttractionRepository(
            [factories.attraction(node_id="a1", name="Space Mountain")]
        )
    )

    reason = ReplanReasonUseCase(factory_for(deps)).execute([_event(attraction_id="a1")])

    assert reason.text == "Updated plan because Space Mountain is down."


# --- diff ----------------------------------------------------------------------------------------------


def test_diff_lists_what_the_replan_added_and_removed():
    current = _plan("plan_1", _stop("a1", 0), _stop("a2", 40))
    candidate = _plan("plan_2", _stop("a1", 0), _stop("a3", 40))

    diff = DiffPlanUseCase().execute(current_plan=current, candidate_plan=candidate)

    assert diff.stops_removed and diff.stops_added
    assert any("a2" in label for label in diff.stops_removed)
    assert any("a3" in label for label in diff.stops_added)


def test_diff_of_identical_plans_is_empty():
    plan = _plan()

    assert DiffPlanUseCase().execute(current_plan=plan, candidate_plan=plan) == PlanDiff()


# --- explanations ---------------------------------------------------------------------------------------


VALID = CheckResult(valid=True)
INVALID = CheckResult(
    valid=False, violations=[ConstraintViolation(rule=RuleId.DATA_FRESHNESS, message="stale")]
)


def test_a_replan_explanation_leads_with_why_and_what_changed():
    diff = PlanDiff(stops_added=["a3"], stops_removed=["a1"])

    text = explain_replan(_plan(), VALID, NAMES, reason="Updated plan because X.", diff=diff)

    lines = text.splitlines()
    assert lines[0] == "Updated plan because X."
    assert "Added: Pirates." in lines
    assert "Removed: Space Mountain." in lines


def test_a_replan_that_only_shifted_times_says_so():
    text = explain_replan(_plan(), VALID, NAMES, reason="why", diff=PlanDiff())

    assert "No stops changed; only times shifted." in text


def test_a_replan_is_only_explained_after_it_passed_the_checker():
    deps = make_deps()

    with pytest.raises(ExplainBeforeValidationError):
        ExplainPlanUseCase(factory_for(deps)).prepare(
            _plan(), INVALID, reason="why", diff=PlanDiff()
        )


# --- active plan and pending proposal --------------------------------------------------------------------


def test_the_active_plan_is_what_the_repository_says():
    deps = make_deps()
    use_case = ActivePlanUseCase(factory_for(deps))
    assert use_case.execute(THREAD) is None

    plan = _plan()
    deps.plans.save(THREAD, plan)
    assert use_case.execute(THREAD) is None  # saved, not activated

    deps.proposals.save(THREAD, factories.proposal(candidate_plan_id=plan.plan_id))
    deps.proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)
    deps.plans.activate(THREAD, plan.plan_id, at=NOW)
    assert use_case.execute(THREAD) == plan
    assert use_case.execute("other-thread") is None


def test_no_pending_proposal_means_none():
    assert PendingProposalUseCase(factory_for(make_deps())).execute(THREAD) is None


def test_the_pending_proposal_comes_with_its_plan():
    deps = make_deps()
    plan = _plan("plan_9")
    deps.plans.save(THREAD, plan)
    deps.proposals.save(THREAD, factories.proposal(proposal_id="p9", candidate_plan_id="plan_9"))

    pending = PendingProposalUseCase(factory_for(deps)).execute(THREAD)

    assert pending is not None
    assert (pending.proposal.proposal_id, pending.candidate_plan) == ("p9", plan)
    assert PendingProposalUseCase(factory_for(deps)).execute("other") is None


def test_a_pending_proposal_whose_plan_is_missing_is_skipped():
    deps = make_deps()
    deps.proposals.save(THREAD, factories.proposal(candidate_plan_id="ghost"))

    assert PendingProposalUseCase(factory_for(deps)).execute(THREAD) is None


def test_a_resolved_proposal_is_not_pending():
    deps = make_deps()
    deps.plans.save(THREAD, _plan("plan_1"))
    deps.proposals.save(THREAD, factories.proposal(candidate_plan_id="plan_1"))
    deps.proposals.resolve("prop_1", ApprovalStatus.REJECTED, at=NOW)

    assert PendingProposalUseCase(factory_for(deps)).execute(THREAD) is None


# --- ProposePlanUseCase for a replan ---------------------------------------------------------------------


def _propose(deps, plan, **kw):
    return ProposePlanUseCase(factory_for(deps)).execute(
        THREAD, plan, proposal_id="prop_new", reason="why", explanation="because", at=NOW, **kw
    )


def test_an_initial_proposal_is_based_on_itself_with_no_trigger():
    deps = make_deps()
    plan = _plan("plan_1")

    proposal = _propose(deps, plan)

    assert proposal.base_plan_id == "plan_1"
    assert proposal.triggering_event_id is None
    assert proposal.diff == PlanDiff()


def test_a_replan_proposal_records_base_diff_and_trigger():
    deps = make_deps()
    diff = PlanDiff(stops_removed=["a1"])

    proposal = _propose(
        deps, _plan("plan_2"), base_plan_id="plan_1", diff=diff, triggering_event_id="ev_1"
    )

    assert (proposal.base_plan_id, proposal.candidate_plan_id) == ("plan_1", "plan_2")
    assert proposal.diff == diff
    assert proposal.triggering_event_id == "ev_1"
    assert proposal.approval_status is ApprovalStatus.PENDING


def test_proposing_stores_no_progress_for_the_candidate():
    deps = make_deps()
    deps.plans.save(THREAD, _plan("plan_1"))
    state = factories.execution_state(plan_id="plan_1")
    deps.execution_states.save(state)

    _propose(deps, _plan("plan_2"), base_plan_id="plan_1")

    assert deps.execution_states.get("plan_2") is None
    assert deps.execution_states.get("plan_1") == state


def test_an_initial_proposal_leaves_its_own_progress_alone():
    deps = make_deps()
    deps.plans.save(THREAD, _plan("plan_1"))
    state = factories.execution_state(plan_id="plan_1")
    deps.execution_states.save(state)

    _propose(deps, _plan("plan_1"))

    assert deps.execution_states.get("plan_1") == state


def test_proposing_supersedes_the_one_that_was_waiting():
    deps = make_deps()
    first = _propose(deps, _plan("plan_2"), base_plan_id="plan_1")
    deps.proposals.rows[first.proposal_id] = (THREAD, first)

    ProposePlanUseCase(factory_for(deps)).execute(
        THREAD, _plan("plan_3"), proposal_id="prop_3", reason="r", explanation="e", at=NOW,
        base_plan_id="plan_1",
    )

    assert deps.proposals.get(first.proposal_id).approval_status is ApprovalStatus.SUPERSEDED
    assert [p.proposal_id for p in deps.proposals.list_pending(THREAD)] == ["prop_3"]


# --- state helpers -----------------------------------------------------------------------------------------


def test_the_active_plan_update_only_mirrors_the_plan():
    plan = _plan()

    assert active_plan_update(plan) == {"current_plan": plan}


def test_discarding_a_proposal_leaves_the_active_plan_alone():
    update = discarded_proposal_update()

    assert update == {"candidate_plan": None, "proposal": None, "approval": None}
    assert "current_plan" not in update

