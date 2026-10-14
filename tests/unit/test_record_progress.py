"""RecordProgressUseCase: completed / skipped stops into the progress and the behavior log."""

from datetime import timedelta

import factories
import pytest
from planning_support import factory_for, make_deps

from parkmind.core.contracts import (
    ApprovalStatus,
    BehaviorEventType,
    GuestRole,
    PlanExecutionState,
    StopKind,
)
from parkmind.services.use_cases.record_progress import (
    InvalidProgressError,
    ProgressProblem,
    RecordProgressUseCase,
    StopOutcome,
)
from parkmind.services.use_cases.replan import effective_execution_state

NOW = factories.NOW
PARTY = [
    factories.guest(guest_id="g1"),
    factories.guest(guest_id="g2", role=GuestRole.CHILD, height_cm=110),
]
PLAN = factories.plan(
    stops=[
        factories.stop(node_id="a1", served_guests=[]),
        factories.stop(node_id="a2", served_guests=["g1"]),
        factories.stop(node_id="lunch", kind=StopKind.MEAL, served_guests=[]),
        factories.stop(node_id="rest", kind=StopKind.REST, served_guests=[]),
    ],
    per_guest_satisfaction={"g1": 0.9, "g2": 0.7},
)


@pytest.fixture
def deps():
    deps = make_deps()
    deps.plans.save("t1", PLAN)
    deps.proposals.save("t1", factories.proposal())
    deps.proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)
    deps.plans.activate("t1", "plan_1", at=NOW)
    return deps


def _report(deps, node_id, outcome, *, plan_id="plan_1", at=NOW):
    return RecordProgressUseCase(factory_for(deps)).execute(
        "t1", plan_id, node_id, outcome, at=at, guests=PARTY
    )


@pytest.mark.parametrize(
    ("outcome", "field", "event_type"),
    [
        (
            StopOutcome.COMPLETED,
            "completed_stop_ids",
            BehaviorEventType.ATTRACTION_COMPLETED,
        ),
        (StopOutcome.SKIPPED, "skipped_stop_ids", BehaviorEventType.ATTRACTION_SKIPPED),
    ],
)
def test_a_reported_stop_joins_the_progress_and_the_log_of_each_guest(
    deps, outcome, field, event_type
):
    recorded = _report(deps, "a1", outcome)

    assert recorded.changed
    assert getattr(deps.execution_states.get("plan_1"), field) == ["a1"]
    for guest_id in ("t1:g1", "t1:g2"):
        (entry,) = deps.behavior.get(guest_id).entries
        assert entry.event_type is event_type
        assert (entry.plan_id, entry.attraction_ids, entry.timestamp) == (
            "plan_1",
            ["a1"],
            NOW,
        )
        assert entry.entry_id == f"plan_1:a1:{outcome.value}"


def test_only_the_guests_a_stop_served_get_the_entry(deps):
    _report(deps, "a2", StopOutcome.COMPLETED)

    assert len(deps.behavior.get("t1:g1").entries) == 1
    assert deps.behavior.get("t1:g2").entries == []


def test_the_log_keys_guests_by_thread_and_keeps_only_their_role(deps):
    _report(deps, "a1", StopOutcome.COMPLETED)

    assert deps.behavior.get("g1").entries == []
    child = deps.guests.get("t1:g2")
    assert (
        child is not None and child.role is GuestRole.CHILD and child.height_cm is None
    )


@pytest.mark.parametrize("node_id", ["lunch", "rest"])
def test_a_meal_or_rest_stop_changes_the_progress_but_logs_no_behavior(deps, node_id):
    _report(deps, node_id, StopOutcome.COMPLETED)

    assert deps.execution_states.get("plan_1").completed_stop_ids == [node_id]
    assert deps.behavior.get("t1:g1").entries == []
    assert deps.guests.get("t1:g1") is None


def test_the_same_report_twice_writes_nothing_the_second_time(deps):
    first = _report(deps, "a1", StopOutcome.COMPLETED)
    saves = len(deps.behavior.get("t1:g1").entries)
    again = _report(deps, "a1", StopOutcome.COMPLETED, at=NOW + timedelta(minutes=5))

    assert first.changed and not again.changed
    stored = deps.execution_states.get("plan_1")
    assert stored.completed_stop_ids == ["a1"]
    assert stored.as_of == NOW  # nothing was saved again
    assert again.execution_state == stored
    assert len(deps.behavior.get("t1:g1").entries) == saves == 1


def test_the_read_modify_write_holds_the_plans_lock(deps):
    """Two reports, or a report and an event, never overwrite each other (Postgres test)."""
    _report(deps, "a1", StopOutcome.COMPLETED)

    assert deps.execution_states.locked == ["plan_1"]


@pytest.mark.parametrize(
    ("first", "then"),
    [
        (StopOutcome.COMPLETED, StopOutcome.SKIPPED),
        (StopOutcome.SKIPPED, StopOutcome.COMPLETED),
    ],
)
def test_a_stop_cannot_be_both_completed_and_skipped(deps, first, then):
    _report(deps, "a1", first)

    with pytest.raises(InvalidProgressError) as raised:
        _report(deps, "a1", then)

    assert raised.value.problem is ProgressProblem.OTHER_OUTCOME_RECORDED
    assert len(deps.behavior.get("t1:g1").entries) == 1


def test_reports_add_up_and_keep_what_was_stored(deps):
    deps.execution_states.save(
        PlanExecutionState(
            plan_id="plan_1",
            current_location_node_id="a1",
            walking_minutes_consumed={"g1": 12.0},
            as_of=NOW,
        )
    )

    _report(deps, "a1", StopOutcome.COMPLETED, at=NOW + timedelta(minutes=30))
    _report(deps, "a2", StopOutcome.SKIPPED, at=NOW + timedelta(minutes=40))

    state = deps.execution_states.get("plan_1")
    assert (state.completed_stop_ids, state.skipped_stop_ids) == (["a1"], ["a2"])
    assert state.current_location_node_id == "a1"
    assert state.walking_minutes_consumed == {"g1": 12.0}
    assert state.as_of == NOW + timedelta(minutes=40)


def test_a_late_report_is_kept_without_moving_the_progress_back(deps):
    _report(deps, "a1", StopOutcome.COMPLETED, at=NOW + timedelta(hours=1))
    _report(deps, "a2", StopOutcome.COMPLETED, at=NOW)

    state = deps.execution_states.get("plan_1")
    assert state.completed_stop_ids == ["a1", "a2"]
    assert state.as_of == NOW + timedelta(hours=1)


def test_only_a_stop_of_the_active_plan_can_be_reported(deps):
    deps.plans.save("t1", factories.plan(plan_id="plan_2"))

    with pytest.raises(InvalidProgressError) as not_active:
        _report(deps, "a1", StopOutcome.COMPLETED, plan_id="plan_2")
    with pytest.raises(InvalidProgressError) as not_a_stop:
        _report(deps, "id-unknown", StopOutcome.COMPLETED)

    assert not_active.value.problem is ProgressProblem.NOT_THE_ACTIVE_PLAN
    assert not_a_stop.value.problem is ProgressProblem.NOT_IN_PLAN
    assert deps.execution_states.get("plan_1") is None
    assert deps.behavior.get("t1:g1").entries == []


def test_nothing_is_recorded_for_a_thread_without_an_active_plan():
    deps = make_deps()
    deps.plans.save("t1", PLAN)

    with pytest.raises(InvalidProgressError) as raised:
        _report(deps, "a1", StopOutcome.COMPLETED)

    assert raised.value.problem is ProgressProblem.NOT_THE_ACTIVE_PLAN


def test_a_partial_report_never_puts_departed_stops_back_in_a_replan(deps):
    """Reporting only a2 as skipped keeps a1, lunch and rest, which have departed, as done."""
    later = NOW + timedelta(hours=3)  # every stop of PLAN has departed by then
    assumed = effective_execution_state(PLAN, None, later)
    assert assumed.completed_stop_ids == ["a1", "a2", "lunch", "rest"]

    _report(deps, "a2", StopOutcome.SKIPPED)

    reported = effective_execution_state(
        PLAN, deps.execution_states.get("plan_1"), later
    )
    assert reported.completed_stop_ids == ["a1", "lunch", "rest"]
    assert reported.skipped_stop_ids == ["a2"]


def test_progress_is_logged_per_guest_so_the_party_is_required(deps):
    with pytest.raises(InvalidProgressError) as raised:
        RecordProgressUseCase(factory_for(deps)).execute(
            "t1", "plan_1", "a1", StopOutcome.COMPLETED, at=NOW, guests=[]
        )

    assert raised.value.problem is ProgressProblem.NO_GUESTS


def test_a_report_time_without_a_timezone_is_refused(deps):
    with pytest.raises(InvalidProgressError) as raised:
        _report(deps, "a1", StopOutcome.COMPLETED, at=NOW.replace(tzinfo=None))

    assert raised.value.problem is ProgressProblem.NAIVE_TIME
    assert deps.execution_states.get("plan_1") is None


@pytest.mark.parametrize(
    "party",
    [
        [factories.guest(guest_id="g1")],  # g2 of the plan is missing
        [*PARTY, factories.guest(guest_id="g9")],  # g9 isn't in the plan
        [factories.guest(guest_id="g1"), factories.guest(guest_id="g3")],
    ],
    ids=["missing", "extra", "different"],
)
def test_a_party_that_is_not_the_plans_is_refused_and_nothing_is_logged(deps, party):
    with pytest.raises(InvalidProgressError) as raised:
        RecordProgressUseCase(factory_for(deps)).execute(
            "t1", "plan_1", "a1", StopOutcome.COMPLETED, at=NOW, guests=party
        )

    assert raised.value.problem is ProgressProblem.PARTY_MISMATCH
    assert deps.execution_states.get("plan_1") is None
    assert deps.guests.get("t1:g9") is None and deps.guests.get("t1:g3") is None


def test_a_served_guest_the_party_lacks_is_a_party_mismatch(deps):
    """The plan's ``served_guests`` name its party too."""
    plan = factories.plan(
        plan_id="plan_9",
        stops=[factories.stop(node_id="a1", served_guests=["g1", "g3"])],
        per_guest_satisfaction={},
    )
    deps.plans.save("t9", plan)
    deps.proposals.save(
        "t9", factories.proposal(proposal_id="prop_9", candidate_plan_id="plan_9")
    )
    deps.proposals.resolve("prop_9", ApprovalStatus.APPROVED, at=NOW)
    deps.plans.activate("t9", "plan_9", at=NOW)

    with pytest.raises(InvalidProgressError) as raised:
        RecordProgressUseCase(factory_for(deps)).execute(
            "t9", "plan_9", "a1", StopOutcome.COMPLETED, at=NOW, guests=PARTY
        )

    assert raised.value.problem is ProgressProblem.PARTY_MISMATCH
