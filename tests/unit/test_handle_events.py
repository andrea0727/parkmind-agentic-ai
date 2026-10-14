"""HandleEventsUseCase: record classified events, supersede, update execution state."""

from datetime import datetime, timedelta

import factories
import pytest
from planning_support import PlanningAttractionRepository, factory_for, make_deps

from parkmind.core.contracts import (
    PARK_TZ,
    ApprovalStatus,
    EventSeverity,
    EventSource,
    EventThresholds,
    EventType,
    FairnessConfig,
    GroupObjective,
    HardConstraintSet,
    StopKind,
)
from parkmind.services.use_cases.handle_events import HandleEventsUseCase

NOW = datetime(2026, 9, 16, 10, 30, tzinfo=PARK_TZ)
THREAD = "thread-1"


def _stop(node_id: str, start_minutes: int, walk: float = 10.0):
    arrival = NOW + timedelta(minutes=start_minutes)
    return factories.stop(
        node_id=node_id,
        kind=StopKind.ATTRACTION,
        arrival_time=arrival,
        departure_time=arrival + timedelta(minutes=45),
        walking_minutes=walk,
    )


PLAN = factories.plan(
    plan_id="plan_active",
    stops=[_stop("space", 0), _stop("jungle", 60), _stop("pirates", 120)],
    unmet_must_do=["tron"],
)
CONSTRAINTS = factories.party_constraints(must_do=["space", "tron"])
OBJECTIVE = GroupObjective(
    objective_version="t",
    weights={},
    hard_constraints=HardConstraintSet(),
    fairness=FairnessConfig(lambda_fairness=0.3, min_satisfaction_floor=0.0),
    event_thresholds=EventThresholds(queue_spike_minutes=20, fatigue_threshold=0.7),
)
CATALOG = [
    factories.attraction(node_id=node_id, name=node_id, outdoor=node_id == "jungle")
    for node_id in ("space", "jungle", "pirates", "tron", "elsewhere")
]


@pytest.fixture
def deps():
    deps = make_deps(attractions=PlanningAttractionRepository(CATALOG))
    deps.plans.save(THREAD, PLAN)
    deps.proposals.save(
        THREAD,
        factories.proposal(proposal_id="prop_pending", candidate_plan_id="plan_x"),
    )
    deps.sessions.put(
        THREAD, factories.accessibility(guest_id="g2", daily_walking_limit_minutes=90)
    )  # type: ignore[attr-defined]
    return deps


def _handle(deps, candidates, *, confirmed=True, now=NOW, ref=("g2",)):
    return HandleEventsUseCase(factory_for(deps)).execute(
        THREAD,
        candidates,
        plan=PLAN,
        constraints=CONSTRAINTS,
        objective=OBJECTIVE,
        accessibility_ref=list(ref),
        confirmed=confirmed,
        now=now,
    )


def _down(attraction: str, source=EventSource.MONITOR, minutes: int = 0):
    return factories.event(
        event_id=f"raw-{attraction}-{source.value}",
        type=EventType.ATTRACTION_DOWN,
        source=source,
        attraction_id=attraction,
        timestamp=NOW + timedelta(minutes=minutes),
    )


def _pending(deps):
    return [p.proposal_id for p in deps.proposals.list_pending(THREAD)]


def test_a_significant_event_supersedes_the_pending_proposal(deps):
    handled = _handle(deps, [_down("space")])

    assert handled.requires_replan is True
    assert handled.superseded == ["prop_pending"]
    assert _pending(deps) == []
    assert (
        deps.proposals.get("prop_pending").approval_status is ApprovalStatus.SUPERSEDED
    )


def test_an_unimportant_change_keeps_the_pending_proposal(deps):
    handled = _handle(deps, [_down("elsewhere")])

    assert [e.severity for e in handled.events] == [EventSeverity.LOW]
    assert handled.superseded == [] and _pending(deps) == ["prop_pending"]


def test_a_duplicate_of_a_recorded_event_neither_records_nor_supersedes(deps):
    first = _handle(deps, [_down("space")])
    deps.proposals.save(
        THREAD, factories.proposal(proposal_id="prop_two", candidate_plan_id="plan_y")
    )

    again = _handle(deps, [_down("space", source=EventSource.USER, minutes=10)])

    assert first.superseded == ["prop_pending"]
    assert again.events == [] and again.duplicates == 1
    assert again.superseded == [] and _pending(deps) == ["prop_two"]
    assert len(deps.events.list_for_thread(THREAD)) == 1


def test_every_recorded_event_carries_severity_and_requires_replan(deps):
    _handle(deps, [_down("space"), _down("elsewhere")])

    stored = deps.events.list_for_thread(THREAD)
    assert len(stored) == 2
    assert all(e.severity is not None and e.requires_replan is not None for e in stored)


def test_a_relocation_is_persisted_and_does_not_supersede(deps):
    moved = factories.event(
        event_id="raw-move",
        type=EventType.PARTY_RELOCATED,
        source=EventSource.USER,
        attraction_id=None,
        location_node_id="frontierland",
        confidence=0.95,
        timestamp=NOW,
    )

    handled = _handle(deps, [moved])

    assert handled.requires_replan is False and handled.superseded == []
    assert (
        deps.execution_states.get(PLAN.plan_id).current_location_node_id
        == "frontierland"
    )


def _fatigue(confidence: float = 0.9, guest: str = "g2"):
    return factories.event(
        event_id="raw-tired",
        type=EventType.GUEST_FATIGUE,
        source=EventSource.USER,
        attraction_id=None,
        guest_id=guest,
        confidence=confidence,
        timestamp=NOW,
    )


def test_fatigue_caps_walking_from_the_session_store_limit(deps):
    deps.execution_states.save(
        factories.execution_state(
            plan_id=PLAN.plan_id,
            completed_stop_ids=[],
            as_of=NOW - timedelta(minutes=1),
            walking_minutes_consumed={"g2": 85.0},
            remaining_walking_cap_minutes={},
        )
    )

    handled = _handle(deps, [_fatigue()])

    # 90 (SessionStore) - 85 walked = 5, under half of the 30 minutes left.
    assert deps.execution_states.get(PLAN.plan_id).remaining_walking_cap_minutes == {
        "g2": 5.0
    }
    assert handled.superseded == ["prop_pending"]


def test_without_the_record_fatigue_needs_confirmation_and_the_plan_term_caps(deps):
    """Review #86 (I5): the record of g3 cannot be read, so the limit is
    unknown. The report fails closed -- it needs confirmation, as with a known
    limit -- and once confirmed only the plan term can cap."""
    held = _handle(deps, [_fatigue(guest="g3")], confirmed=False, ref=("g2", "g3"))
    assert [e.guest_id for e in held.awaiting_confirmation] == ["g3"]

    handled = _handle(deps, [_fatigue(guest="g3")], ref=("g2", "g3"))

    assert handled.requirements_missing == ("g3",)
    assert deps.execution_states.get(PLAN.plan_id).remaining_walking_cap_minutes == {
        "g3": 15.0
    }


def test_an_unconfirmed_fatigue_report_writes_nothing_and_awaits_confirmation(deps):
    handled = _handle(deps, [_fatigue()], confirmed=False)

    assert [e.type for e in handled.awaiting_confirmation] == [EventType.GUEST_FATIGUE]
    assert handled.events == [] and handled.superseded == []
    assert deps.events.list_for_thread(THREAD) == []
    assert deps.execution_states.get(PLAN.plan_id) is None
    assert _pending(deps) == ["prop_pending"]


def test_a_doubtful_report_does_not_hold_back_the_rest_of_the_batch(deps):
    """Review #86 (I6): a monitor closure and a low-confidence fatigue report in
    one call. The closure is recorded and supersedes; the report waits."""
    handled = _handle(deps, [_down("space"), _fatigue(confidence=0.5)], confirmed=False)

    assert [e.type for e in handled.events] == [EventType.ATTRACTION_DOWN]
    assert handled.superseded == ["prop_pending"]
    assert [e.type for e in handled.awaiting_confirmation] == [EventType.GUEST_FATIGUE]
    assert [e.type for e in deps.events.list_for_thread(THREAD)] == [
        EventType.ATTRACTION_DOWN
    ]


def test_a_doubtful_report_of_a_known_closure_asks_nothing(deps):
    _handle(deps, [_down("space")])

    doubtful = _down("space", source=EventSource.USER, minutes=5).model_copy(
        update={"confidence": 0.4}
    )
    handled = _handle(deps, [doubtful], confirmed=False)

    assert handled.awaiting_confirmation == [] and handled.duplicates == 1


def test_behind_schedule_is_derived_from_the_execution_state(deps):
    deps.execution_states.save(
        factories.execution_state(
            plan_id=PLAN.plan_id, completed_stop_ids=["space"], as_of=NOW
        )
    )

    # jungle should have been left at +105.
    handled = _handle(deps, [], now=NOW + timedelta(minutes=123))

    [event] = handled.events
    assert (event.type, event.attraction_id, event.minutes_behind) == (
        EventType.BEHIND_SCHEDULE,
        "jungle",
        18.0,
    )
    assert handled.superseded == ["prop_pending"]


def test_a_small_delay_does_not_use_up_the_stop(deps):
    """Review #86 (N1): +3 min is nothing; +20 min still replans."""
    deps.execution_states.save(
        factories.execution_state(
            plan_id=PLAN.plan_id, completed_stop_ids=["space"], as_of=NOW
        )
    )

    small = _handle(deps, [], now=NOW + timedelta(minutes=108))
    large = _handle(deps, [], now=NOW + timedelta(minutes=125))

    assert small.events == [] and small.superseded == []
    assert [e.requires_replan for e in large.events] == [True]
    assert large.superseded == ["prop_pending"]


def test_only_the_first_crossing_of_the_threshold_replans(deps):
    deps.execution_states.save(
        factories.execution_state(
            plan_id=PLAN.plan_id, completed_stop_ids=["space"], as_of=NOW
        )
    )

    # jungle leaves at +105: 3, 8, 16 and 30 minutes behind.
    runs = [_handle(deps, [], now=NOW + timedelta(minutes=m)) for m in (108, 113, 121)]
    deps.proposals.save(
        THREAD, factories.proposal(proposal_id="prop_replan", candidate_plan_id="p2")
    )
    later = _handle(deps, [], now=NOW + timedelta(minutes=135))

    assert [len(r.events) for r in runs] == [0, 0, 1]
    assert runs[2].superseded == ["prop_pending"]
    assert later.events == [] and later.superseded == []
    assert _pending(deps) == ["prop_replan"]


def test_staying_behind_supersedes_only_once(deps):
    deps.execution_states.save(
        factories.execution_state(
            plan_id=PLAN.plan_id, completed_stop_ids=["space"], as_of=NOW
        )
    )
    _handle(deps, [], now=NOW + timedelta(minutes=123))
    deps.proposals.save(
        THREAD, factories.proposal(proposal_id="prop_replan", candidate_plan_id="p2")
    )

    still = [_handle(deps, [], now=NOW + timedelta(minutes=m)) for m in (154, 185)]

    assert [h.superseded for h in still] == [[], []]
    assert _pending(deps) == ["prop_replan"]


def test_a_state_created_by_a_relocation_raises_no_behind_schedule_later(deps):
    """Review #86 (I2): the relocation creates the execution state with nothing
    completed; the next cycle, hours later, must not call the party late."""
    moved = factories.event(
        event_id="raw-move",
        type=EventType.PARTY_RELOCATED,
        source=EventSource.USER,
        attraction_id=None,
        location_node_id="frontierland",
        confidence=0.95,
        timestamp=NOW,
    )
    _handle(deps, [moved])

    later = _handle(deps, [], now=NOW + timedelta(hours=3))

    assert later.events == [] and later.superseded == []
    assert _pending(deps) == ["prop_pending"]


def test_an_available_unmet_must_do_replans_and_another_does_not(deps):
    def available(attraction):
        return factories.event(
            event_id=f"raw-up-{attraction}",
            type=EventType.ATTRACTION_AVAILABLE,
            attraction_id=attraction,
            timestamp=NOW,
        )

    assert _handle(deps, [available("elsewhere")]).superseded == []
    assert _handle(deps, [available("tron")]).superseded == ["prop_pending"]


def test_a_recorded_fatigue_asks_the_next_plan_to_open_with_a_rest(deps):
    handled = _handle(deps, [_fatigue()])

    assert handled.rest_first is True


def test_a_closure_alone_does_not_force_a_rest(deps):
    handled = _handle(deps, [_down("space")])

    assert handled.rest_first is False


def test_a_report_set_aside_for_confirmation_forces_no_rest_yet(deps):
    handled = _handle(deps, [_fatigue()], confirmed=False)

    assert handled.rest_first is False
