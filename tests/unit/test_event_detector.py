"""Snapshot-to-event detection (P0-33): transitions, freshness, determinism."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import factories
import pytest
from capture import capture

from parkmind.core.contracts import (
    PARK_TZ,
    AttractionStatus,
    EventSource,
    EventType,
    StopKind,
    WaitEstimate,
    WeatherHour,
)
from parkmind.services.clients.themeparks_normalize import parse_live
from parkmind.services.personalization.group_preference_resolver import (
    _QUEUE_SPIKE_MINUTES_RANGE,
)
from parkmind.services.planning.event_detector import (
    DEFAULT_DETECTOR_CONFIG,
    detect_behind_schedule,
    detect_events,
)

CASES = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "events"
        / "detector_cases.json"
    ).read_text(encoding="utf-8")
)["cases"]

NOW = datetime(2026, 9, 16, 10, 31, tzinfo=PARK_TZ)
BEFORE = NOW - timedelta(minutes=6)
TAKEN = NOW - timedelta(minutes=1)
FRESH = NOW - timedelta(minutes=2)


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _context(spec: dict[str, Any], snapshot_id: str):
    statuses = {k: AttractionStatus(v) for k, v in spec["statuses"].items()}
    return factories.live_context(
        snapshot_id=snapshot_id,
        retrieved_at=_at(spec["retrieved_at"]),
        statuses=statuses,
        waits={
            k: WaitEstimate(
                attraction_id=k,
                wait_minutes=float(v),
                status=statuses.get(k, AttractionStatus.OPERATING),
            )
            for k, v in spec["waits"].items()
        },
        weather=[
            WeatherHour(
                timestamp=_at(hour),
                condition="rain" if p >= 0.5 else "clouds",
                temperature_f=80.0,
                precipitation_probability=p,
            )
            for hour, p in spec["rain"].items()
        ],
    )


def _run_case(case: dict[str, Any]):
    return detect_events(
        _context(case["previous"], "prev"),
        _context(case["current"], "curr"),
        now=_at(case["now"]),
        reading_times={k: _at(v) for k, v in case["reading_times"].items()},
    )


def _summary(event) -> dict[str, Any]:
    out: dict[str, Any] = {"type": event.type.value}
    if event.attraction_id is not None:
        out["attraction_id"] = event.attraction_id
    if event.delta_minutes is not None:
        out["delta_minutes"] = event.delta_minutes
    if event.affected_hours is not None:
        out["affected_hours"] = [h.isoformat() for h in event.affected_hours]
        out["confidence"] = event.confidence
    return out


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fixture_snapshots_yield_exactly_the_hand_written_events(case):
    assert [_summary(e) for e in _run_case(case)] == case["expected"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_event_generation_is_deterministic(case):
    assert _run_case(case) == _run_case(case)


def test_detected_events_are_unclassified_monitor_events():
    events = _run_case(CASES[0])

    assert events
    for event in events:
        assert event.source is EventSource.MONITOR
        assert event.severity is None
        assert event.requires_replan is None


def _pair(before: AttractionStatus, after: AttractionStatus):
    previous = factories.live_context(
        snapshot_id="prev", retrieved_at=BEFORE, statuses={"a1": before}, waits={}
    )
    current = factories.live_context(
        snapshot_id="curr", retrieved_at=TAKEN, statuses={"a1": after}, waits={}
    )
    return previous, current


@pytest.mark.parametrize(
    "after",
    [AttractionStatus.DOWN, AttractionStatus.CLOSED, AttractionStatus.REFURBISHMENT],
)
def test_any_unavailable_status_after_operating_is_attraction_down(after):
    previous, current = _pair(AttractionStatus.OPERATING, after)

    events = detect_events(previous, current, now=NOW, reading_times={"a1": FRESH})

    assert [e.type for e in events] == [EventType.ATTRACTION_DOWN]


def test_a_status_that_did_not_change_is_no_event():
    previous, current = _pair(AttractionStatus.DOWN, AttractionStatus.DOWN)

    assert detect_events(previous, current, now=NOW, reading_times={"a1": FRESH}) == []


@pytest.mark.parametrize(
    "reading_time",
    [
        datetime(2026, 7, 28, 4, 4, tzinfo=PARK_TZ),  # months old
        NOW - timedelta(minutes=31),  # just outside the window
        None,  # unknown
    ],
    ids=["months_old", "outside_window", "unknown"],
)
def test_a_reading_that_is_not_fresh_never_raises_an_event(reading_time):
    previous, current = _pair(AttractionStatus.OPERATING, AttractionStatus.DOWN)

    events = detect_events(
        previous, current, now=NOW, reading_times={"a1": reading_time}
    )

    assert events == []


def test_a_fresh_reading_published_late_by_the_provider_still_counts():
    """Review #86 (I4): a cached ``lastUpdated`` older than the previous snapshot
    but inside the window. Dropping it would lose the change for good: the next
    run sees DOWN -> DOWN."""
    previous, current = _pair(AttractionStatus.OPERATING, AttractionStatus.DOWN)
    late = BEFORE - timedelta(minutes=12)

    events = detect_events(previous, current, now=NOW, reading_times={"a1": late})

    assert [e.type for e in events] == [EventType.ATTRACTION_DOWN]


def test_a_stale_current_snapshot_raises_nothing():
    previous, current = _pair(AttractionStatus.OPERATING, AttractionStatus.DOWN)

    later = TAKEN + timedelta(minutes=31)
    assert (
        detect_events(previous, current, now=later, reading_times={"a1": FRESH}) == []
    )


def test_a_snapshot_dated_after_now_raises_nothing():
    previous, current = _pair(AttractionStatus.OPERATING, AttractionStatus.DOWN)

    earlier = TAKEN - timedelta(minutes=5)
    assert (
        detect_events(previous, current, now=earlier, reading_times={"a1": FRESH}) == []
    )


def test_without_a_previous_snapshot_nothing_is_detected():
    _, current = _pair(AttractionStatus.OPERATING, AttractionStatus.DOWN)

    assert detect_events(None, current, now=NOW, reading_times={"a1": FRESH}) == []


def test_a_previous_snapshot_from_long_ago_is_no_baseline():
    """Found live (2026-10-12): the collector had not run since 2026-09-27, and
    every wait compared with that day looked like a spike."""
    previous, current = _pair(AttractionStatus.OPERATING, AttractionStatus.DOWN)
    long_ago = previous.model_copy(update={"retrieved_at": TAKEN - timedelta(days=15)})
    just_inside = previous.model_copy(
        update={"retrieved_at": TAKEN - timedelta(minutes=30)}
    )

    assert detect_events(long_ago, current, now=NOW, reading_times={"a1": FRESH}) == []
    assert detect_events(just_inside, current, now=NOW, reading_times={"a1": FRESH})


def test_out_of_order_snapshots_raise_nothing():
    previous, current = _pair(AttractionStatus.OPERATING, AttractionStatus.DOWN)

    assert detect_events(current, previous, now=NOW, reading_times={"a1": FRESH}) == []


def _waits(before: float, after: float, *, status=AttractionStatus.OPERATING):
    previous = factories.live_context(
        snapshot_id="prev",
        retrieved_at=BEFORE,
        statuses={"a1": AttractionStatus.OPERATING},
        waits={
            "a1": WaitEstimate(
                attraction_id="a1",
                wait_minutes=before,
                status=AttractionStatus.OPERATING,
            )
        },
    )
    current = factories.live_context(
        snapshot_id="curr",
        retrieved_at=TAKEN,
        statuses={"a1": status},
        waits={
            "a1": WaitEstimate(attraction_id="a1", wait_minutes=after, status=status)
        },
    )
    return detect_events(previous, current, now=NOW, reading_times={"a1": FRESH})


def test_a_wait_rise_at_the_floor_is_a_spike_and_just_below_is_not():
    floor = DEFAULT_DETECTOR_CONFIG.spike_floor_minutes

    assert [e.delta_minutes for e in _waits(20, 20 + floor)] == [floor]
    assert _waits(20, 20 + floor - 0.5) == []


def test_the_spike_floor_is_never_above_any_partys_threshold():
    """The policy can only judge a spike the detector reports."""
    assert DEFAULT_DETECTOR_CONFIG.spike_floor_minutes <= min(
        _QUEUE_SPIKE_MINUTES_RANGE
    )


def test_a_wait_on_a_ride_that_is_not_operating_is_no_spike():
    events = _waits(20, 60, status=AttractionStatus.DOWN)

    assert EventType.WAIT_SPIKE not in [e.type for e in events]


def _weather(previous_p: dict[datetime, float], current_p: dict[datetime, float]):
    def hours(probabilities):
        return [
            WeatherHour(
                timestamp=h,
                condition="x",
                temperature_f=80.0,
                precipitation_probability=p,
            )
            for h, p in probabilities.items()
        ]

    previous = factories.live_context(
        snapshot_id="prev", retrieved_at=BEFORE, weather=hours(previous_p)
    )
    current = factories.live_context(
        snapshot_id="curr", retrieved_at=TAKEN, weather=hours(current_p)
    )
    return detect_events(previous, current, now=NOW, reading_times={})


def test_rain_only_counts_inside_the_look_ahead_and_not_in_past_hours():
    past = NOW.replace(minute=0) - timedelta(hours=1)
    this_hour = NOW.replace(minute=0)
    too_far = NOW + timedelta(hours=4)

    dry = {past: 0.1, this_hour: 0.1, too_far: 0.1}

    events = _weather(dry, {past: 0.9, this_hour: 0.8, too_far: 0.9})

    assert [e.affected_hours for e in events] == [[this_hour]]
    assert events[0].confidence == 0.8


def test_rain_already_forecast_in_the_previous_snapshot_is_not_new():
    hour = NOW.replace(minute=0) + timedelta(hours=1)

    assert _weather({hour: 0.6}, {hour: 0.9}) == []


def test_an_hour_the_previous_snapshot_did_not_forecast_is_no_rain_event():
    """Review #86 (I3): a failed weather call leaves ``previous.weather`` empty;
    the hours are unknown then, not dry."""
    hour = NOW.replace(minute=0) + timedelta(hours=1)
    other = hour + timedelta(hours=1)

    assert _weather({}, {hour: 0.8}) == []
    assert _weather({other: 0.1}, {hour: 0.8, other: 0.1}) == []


def test_the_real_capture_s_stale_readings_raise_no_alarm():
    """2026-09-27 ThemeParks payload: a CLOSED show last updated in July and an
    entity from 2022 must stay silent even if the previous snapshot disagrees;
    the freshest real reading still produces its event."""
    live = parse_live(capture("themeparks_live.json"))
    readings = {i: e.observed_at for i, e in live.entities.items()}
    statuses = {i: e.status for i, e in live.entities.items()}
    newest = max((t, i) for i, t in readings.items() if t is not None)
    now = newest[0] + timedelta(minutes=1)
    stale = sorted(
        i for i, t in readings.items() if t is not None and t < now - timedelta(days=30)
    )
    assert stale, "the capture is expected to hold months-old readings"

    def flipped(status: AttractionStatus) -> AttractionStatus:
        return (
            AttractionStatus.DOWN
            if status is AttractionStatus.OPERATING
            else AttractionStatus.OPERATING
        )

    previous = factories.live_context(
        snapshot_id="prev",
        retrieved_at=now - timedelta(minutes=6),
        statuses={i: flipped(statuses[i]) for i in [*stale, newest[1]]},
        waits={},
        weather=[],
    )
    current = factories.live_context(
        snapshot_id="curr",
        retrieved_at=now - timedelta(minutes=1),
        statuses=statuses,
        waits={},
        weather=[],
    )

    events = detect_events(previous, current, now=now, reading_times=readings)

    assert {e.attraction_id for e in events} == {newest[1]}


def _stop(node_id: str, leaves: datetime):
    return factories.stop(
        node_id=node_id,
        kind=StopKind.ATTRACTION,
        arrival_time=leaves - timedelta(minutes=30),
        departure_time=leaves,
    )


def test_behind_schedule_measures_from_the_earliest_unfinished_stop():
    plan = factories.plan(
        stops=[
            _stop("a1", NOW - timedelta(minutes=40)),
            _stop("a2", NOW - timedelta(minutes=18)),
            _stop("a3", NOW + timedelta(minutes=20)),
        ]
    )
    state = factories.execution_state(completed_stop_ids=["a1"])

    event = detect_behind_schedule(plan, state, now=NOW)

    assert event is not None
    assert (event.type, event.attraction_id, event.minutes_behind) == (
        EventType.BEHIND_SCHEDULE,
        "a2",
        18.0,
    )


def test_a_party_on_schedule_or_without_state_is_not_behind():
    plan = factories.plan(
        stops=[
            _stop("a1", NOW - timedelta(minutes=5)),
            _stop("a2", NOW + timedelta(minutes=30)),
        ]
    )

    assert (
        detect_behind_schedule(
            plan, factories.execution_state(completed_stop_ids=["a1"]), now=NOW
        )
        is None
    )
    assert (
        detect_behind_schedule(
            plan,
            factories.execution_state(skipped_stop_ids=["a1"], completed_stop_ids=[]),
            now=NOW,
        )
        is None
    )
    assert detect_behind_schedule(plan, None, now=NOW) is None
    other = factories.execution_state(plan_id="another_plan", completed_stop_ids=[])
    assert detect_behind_schedule(plan, other, now=NOW) is None


def test_now_must_be_timezone_aware():
    previous, current = _pair(AttractionStatus.OPERATING, AttractionStatus.DOWN)

    with pytest.raises(ValueError, match="timezone-aware"):
        detect_events(previous, current, now=NOW.replace(tzinfo=None), reading_times={})
