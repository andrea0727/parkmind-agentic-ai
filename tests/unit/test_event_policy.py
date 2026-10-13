"""EventPolicy (P0-33): section 25's table, the confirmation gate, execution updates."""

from datetime import datetime, timedelta

import factories
import pytest

from parkmind.core.contracts import (
    PARK_TZ,
    EventSeverity,
    EventSource,
    EventThresholds,
    EventType,
    StopKind,
)
from parkmind.services.planning.event_policy import (
    CapWalking,
    ForbidNode,
    ForceRest,
    LiftForbid,
    PartyEventContext,
    PenalizeOutdoor,
    Relocated,
    UnconfirmedEventError,
    apply_decisions,
    apply_execution_updates,
    classify,
    needs_confirmation,
)

NOW = datetime(2026, 9, 16, 10, 30, tzinfo=PARK_TZ)


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
    stops=[
        _stop("done", -90),
        _stop("space", 0),
        _stop("jungle", 60),
        _stop("pirates", 120),
    ],
    unmet_must_do=["tron"],
)


def _party(**overrides) -> PartyEventContext:
    fields = {
        "plan": PLAN,
        "must_do": ["space", "tron", "done"],
        "thresholds": EventThresholds(queue_spike_minutes=20, fatigue_threshold=0.7),
        "outdoor_ids": ["jungle"],
        "execution_state": factories.execution_state(
            completed_stop_ids=["done"],
            walking_minutes_consumed={"g2": 10.0},
            remaining_walking_cap_minutes={},
        ),
        "daily_walking_limits": {"g2": 90},
    }
    return PartyEventContext(**{**fields, **overrides})


def _event(event_type: EventType, **fields):
    return factories.event(
        **{
            "event_id": f"{event_type.value}:x",
            "type": event_type,
            "timestamp": NOW,
            **fields,
        }
    )


def _one(event, party=None, confirmed=True):
    [decision] = classify([event], party=party or _party(), confirmed=confirmed)
    return decision


@pytest.mark.parametrize(
    ("attraction", "severity", "replan"),
    [
        ("space", EventSeverity.HIGH, True),  # a remaining stop
        ("pirates", EventSeverity.HIGH, True),
        ("done", EventSeverity.LOW, False),  # already ridden, even though a must-do
        ("tron", EventSeverity.LOW, False),  # already unmet: nothing changes
        ("elsewhere", EventSeverity.LOW, False),  # not in the plan, not a must-do
    ],
)
def test_attraction_down_is_high_only_for_what_the_party_still_needs(
    attraction, severity, replan
):
    decision = _one(_event(EventType.ATTRACTION_DOWN, attraction_id=attraction))

    assert (decision.event.severity, decision.event.requires_replan) == (
        severity,
        replan,
    )
    assert decision.effects == (ForbidNode(attraction),)


def test_a_down_must_do_not_yet_scheduled_is_high():
    party = _party(plan=factories.plan(stops=[_stop("space", 0)]), must_do=["tron"])

    decision = _one(_event(EventType.ATTRACTION_DOWN, attraction_id="tron"), party)

    assert decision.event.requires_replan is True


def test_attraction_available_for_an_unmet_must_do_requires_replan():
    decision = _one(_event(EventType.ATTRACTION_AVAILABLE, attraction_id="tron"))

    assert (decision.event.severity, decision.event.requires_replan) == (
        EventSeverity.MEDIUM,
        True,
    )
    assert decision.effects == (LiftForbid("tron"),)


@pytest.mark.parametrize("attraction", ["space", "elsewhere", "done"])
def test_attraction_available_for_any_other_attraction_does_not(attraction):
    decision = _one(_event(EventType.ATTRACTION_AVAILABLE, attraction_id=attraction))

    assert (decision.event.severity, decision.event.requires_replan) == (
        EventSeverity.LOW,
        False,
    )


def test_party_relocated_alone_never_requires_replan():
    decision = _one(
        _event(
            EventType.PARTY_RELOCATED,
            source=EventSource.USER,
            location_node_id="frontierland",
            confidence=0.95,
        )
    )

    assert (decision.event.severity, decision.event.requires_replan) == (
        EventSeverity.LOW,
        False,
    )
    assert decision.effects == (Relocated("frontierland"),)


@pytest.mark.parametrize(
    ("attraction", "delta", "replan"),
    [
        ("space", 25.0, True),
        ("space", 20.0, True),  # at the party's threshold
        ("space", 19.5, False),  # below it: unimportant
        ("elsewhere", 60.0, False),  # not a stop the party will visit
        ("done", 60.0, False),
    ],
)
def test_a_wait_spike_matters_only_above_the_partys_threshold_on_a_remaining_stop(
    attraction, delta, replan
):
    event = _event(EventType.WAIT_SPIKE, attraction_id=attraction, delta_minutes=delta)

    assert _one(event).event.requires_replan is replan


def test_the_spike_threshold_is_the_partys_own():
    event = _event(EventType.WAIT_SPIKE, attraction_id="space", delta_minutes=15.0)
    patient = _party(thresholds=EventThresholds(queue_spike_minutes=30))
    sensitive = _party(thresholds=EventThresholds(queue_spike_minutes=10))

    assert _one(event, patient).event.requires_replan is False
    assert _one(event, sensitive).event.requires_replan is True


def test_rain_matters_when_a_remaining_outdoor_stop_falls_in_an_affected_hour():
    jungle_hour = (NOW + timedelta(minutes=60)).replace(minute=0)
    event = _event(
        EventType.RAIN_INCOMING, attraction_id=None, affected_hours=[jungle_hour]
    )

    decision = _one(event)

    assert decision.event.requires_replan is True
    assert decision.effects == (PenalizeOutdoor((jungle_hour,), ("jungle",)),)


def test_rain_over_indoor_stops_only_is_unimportant():
    late = NOW + timedelta(hours=5)
    for hours in (
        [NOW.replace(minute=0)],
        [late],
    ):  # space is indoor; nothing is outdoor late
        event = _event(
            EventType.RAIN_INCOMING, attraction_id=None, affected_hours=hours
        )
        assert _one(event).event.requires_replan is False


@pytest.mark.parametrize(
    ("behind", "replan"), [(14.0, False), (15.0, True), (18.0, True)]
)
def test_behind_schedule_replans_from_fifteen_minutes(behind, replan):
    event = _event(
        EventType.BEHIND_SCHEDULE, attraction_id="space", minutes_behind=behind
    )

    assert _one(event).event.requires_replan is replan


def _fatigue(guest="g2", confidence=0.9):
    return _event(
        EventType.GUEST_FATIGUE,
        attraction_id=None,
        source=EventSource.USER,
        guest_id=guest,
        confidence=confidence,
    )


def test_fatigue_caps_walking_at_half_of_what_is_left_and_forces_a_rest():
    # Remaining stops: space, jungle, pirates -> 30 min of walking; half is 15,
    # well under 90 - 10 walked.
    decision = _one(_fatigue())

    assert (decision.event.severity, decision.event.requires_replan) == (
        EventSeverity.MEDIUM,
        True,
    )
    assert decision.effects == (CapWalking("g2", 15.0), ForceRest("g2"))


def test_the_fatigue_cap_never_exceeds_the_daily_limit_minus_what_was_walked():
    state = factories.execution_state(
        completed_stop_ids=["done"],
        walking_minutes_consumed={"g2": 85.0},
        remaining_walking_cap_minutes={},
    )

    decision = _one(_fatigue(), _party(execution_state=state))

    assert decision.effects[0] == CapWalking("g2", 5.0)


def test_the_fatigue_cap_never_goes_up():
    state = factories.execution_state(
        completed_stop_ids=["done"],
        walking_minutes_consumed={},
        remaining_walking_cap_minutes={"g2": 8.0},
    )

    decision = _one(_fatigue(), _party(execution_state=state))

    assert decision.effects[0] == CapWalking("g2", 8.0)


def test_a_guest_without_a_daily_limit_is_capped_by_the_plan_term_only():
    decision = _one(_fatigue(guest="g1"))

    assert decision.effects[0] == CapWalking("g1", 15.0)


def test_the_fatigue_cap_is_never_negative():
    state = factories.execution_state(
        completed_stop_ids=["done"],
        walking_minutes_consumed={"g2": 95.0},
        remaining_walking_cap_minutes={},
    )

    assert _one(_fatigue(), _party(execution_state=state)).effects[0] == CapWalking(
        "g2", 0.0
    )


def test_events_come_out_classified_and_inputs_are_left_alone():
    event = _event(EventType.ATTRACTION_DOWN, attraction_id="space")

    decision = _one(event)

    assert event.severity is None and event.requires_replan is None
    assert (
        decision.event.severity is not None
        and decision.event.requires_replan is not None
    )


# ---- confirmation gate -----------------------------------------------------


THRESHOLDS = EventThresholds(fatigue_threshold=0.7)


def test_monitor_events_never_need_confirmation():
    event = _event(EventType.ATTRACTION_DOWN, attraction_id="space", confidence=0.1)

    assert needs_confirmation(event, thresholds=THRESHOLDS) is False


def test_a_low_confidence_user_report_needs_confirmation():
    event = _event(
        EventType.PARTY_RELOCATED,
        source=EventSource.USER,
        location_node_id="x",
        confidence=0.5,
    )

    assert needs_confirmation(event, thresholds=THRESHOLDS) is True


def test_fatigue_for_a_guest_with_a_walking_limit_always_needs_confirmation():
    assert needs_confirmation(
        _fatigue(confidence=0.99),
        thresholds=THRESHOLDS,
        daily_walking_limits={"g2": 90},
    )
    assert not needs_confirmation(
        _fatigue(guest="g1", confidence=0.99),
        thresholds=THRESHOLDS,
        daily_walking_limits={"g2": 90},
    )
    assert needs_confirmation(
        _fatigue(guest="g1", confidence=0.72),
        thresholds=EventThresholds(fatigue_threshold=0.8),
    )


def test_fatigue_for_a_guest_whose_requirements_could_not_be_read_needs_confirmation():
    """Review #86 (I5): an unknown limit fails closed, like a known one."""
    assert needs_confirmation(
        _fatigue(confidence=0.99),
        thresholds=THRESHOLDS,
        daily_walking_limits={},
        unread_requirements=["g2"],
    )


def test_an_unconfirmed_event_that_needs_confirmation_is_refused():
    with pytest.raises(UnconfirmedEventError):
        classify([_fatigue()], party=_party(), confirmed=False)


def test_a_confident_user_event_needs_no_confirmation_to_be_classified():
    event = _event(
        EventType.PARTY_RELOCATED,
        source=EventSource.USER,
        location_node_id="x",
        confidence=0.95,
    )

    assert classify([event], party=_party(), confirmed=False)


# ---- execution-state updates -----------------------------------------------


def test_relocation_is_applied_before_the_policy_latest_report_wins():
    state = factories.execution_state(as_of=NOW - timedelta(minutes=30))
    first = _event(
        EventType.PARTY_RELOCATED,
        location_node_id="adventureland",
        timestamp=NOW - timedelta(minutes=5),
    )
    second = _event(
        EventType.PARTY_RELOCATED, location_node_id="frontierland", timestamp=NOW
    )
    second = second.model_copy(update={"event_id": "later"})

    updated = apply_execution_updates(state, [second, first], plan_id="plan_1", now=NOW)

    assert updated is not None
    assert (updated.current_location_node_id, updated.as_of) == ("frontierland", NOW)


def test_without_a_relocation_the_state_is_untouched():
    state = factories.execution_state()

    assert (
        apply_execution_updates(state, [_fatigue()], plan_id="plan_1", now=NOW) is state
    )
    assert apply_execution_updates(None, [], plan_id="plan_1", now=NOW) is None


def test_a_relocation_never_moves_as_of_back():
    state = factories.execution_state(as_of=NOW)
    old = _event(
        EventType.PARTY_RELOCATED,
        location_node_id="x",
        timestamp=NOW - timedelta(hours=1),
    )

    updated = apply_execution_updates(state, [old], plan_id="plan_1", now=NOW)

    assert updated is not None and updated.as_of == NOW


def test_the_walking_cap_lands_in_the_execution_state():
    decisions = classify([_fatigue()], party=_party(), confirmed=True)

    updated = apply_decisions(
        factories.execution_state(remaining_walking_cap_minutes={}),
        decisions,
        plan_id="plan_1",
        now=NOW,
    )

    assert updated is not None and updated.remaining_walking_cap_minutes == {"g2": 15.0}


def test_the_policy_is_deterministic():
    events = [
        _event(EventType.ATTRACTION_DOWN, attraction_id="space"),
        _event(EventType.WAIT_SPIKE, attraction_id="jungle", delta_minutes=40.0),
        _fatigue(),
    ]

    assert classify(events, party=_party(), confirmed=True) == classify(
        events, party=_party(), confirmed=True
    )
