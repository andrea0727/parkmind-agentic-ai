"""Snapshot-to-event detection (section 24, backlog P0-33).

Pure: two consecutive stored snapshots in, candidate ``Event``s out. Nothing
here sets ``severity`` or ``requires_replan`` -- only ``EventPolicy`` does
(section 24 "two sources, one policy") -- and nothing reads the clock: ``now``
is always passed in. Fetching the snapshots and their reading times is
``services/use_cases/monitor_events.py``'s job.

What counts as a change:

* ``ATTRACTION_DOWN``: OPERATING -> DOWN, CLOSED or REFURBISHMENT.
  ``ATTRACTION_AVAILABLE`` [C23]: the reverse. A status missing from the
  previous snapshot is unknown, so it is never a transition.
* ``WAIT_SPIKE``: an OPERATING posted wait that rose by at least
  ``spike_floor_minutes``. The floor is the lowest ``queue_spike`` threshold
  any party can have; whether the spike matters to *this* party is the
  policy's call (section 25, profile-aware thresholds).
* ``RAIN_INCOMING``: look-ahead hours whose precipitation probability crossed
  ``rain_probability`` since the previous snapshot: the hour is in the
  previous forecast, below the threshold. An hour the previous snapshot did not
  forecast (an empty weather list after a failed call) is unknown, not a change.
  Whether an outdoor stop falls in them is again the policy's call.
* ``BEHIND_SCHEDULE`` comes from the party's ``PlanExecutionState``, not from
  snapshots: ``detect_behind_schedule``.

Freshness. No event comes from a current snapshot older than the window at
``now`` (rule 11's 30 minutes), nor from a pair that is not consecutive: a
previous snapshot taken more than ``max_gap`` before the current one (a
collector that stopped for days) is no baseline -- a wait compared with one
from two weeks ago is not a spike. The next run compares against this one. A
status or wait event also needs its own reading to be fresh: the provider's
per-entity reading time (``lastUpdated``, passed in as ``reading_times``) must
be known and within the window at ``now``. It may predate the previous
snapshot -- a provider cache can publish a change late, and the next run would
see no transition -- and ``event_dedup`` absorbs a repeat. ThemeParks keeps
readings months old (a CLOSED show last updated in July, an entity from 2022 in
the 2026-09-27 fixture); those never raise an alarm. An unknown reading time
fails closed: no event. A missed change is caught again by LOAD CONTEXT and
rule 1 at the next (re)plan; a false alarm would wake the party for nothing.

Event ids here are provisional and deterministic; ``event_dedup`` assigns the
cross-source id before anything is recorded.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from parkmind.core.contracts import (
    AttractionStatus,
    Event,
    EventSource,
    EventType,
    LiveContext,
    Plan,
    PlanExecutionState,
)

FRESHNESS_WINDOW = timedelta(minutes=30)
"""Rule 11's status-data window (``latest_snapshot.DEFAULT_MAX_AGE``)."""

UNAVAILABLE = frozenset(
    {AttractionStatus.DOWN, AttractionStatus.CLOSED, AttractionStatus.REFURBISHMENT}
)

_MONITOR_CONFIDENCE = 1.0
"""Status and wait events are observed readings, not estimates."""

IsNew = Callable[[str], bool]


@dataclass(frozen=True)
class DetectorConfig:
    freshness_window: timedelta = FRESHNESS_WINDOW
    clock_skew: timedelta = timedelta(minutes=1)
    """Tolerance between the provider's reading times and ours."""
    max_gap: timedelta = FRESHNESS_WINDOW
    """The longest time between two snapshots that still makes them consecutive."""
    spike_floor_minutes: float = 10.0
    """The lowest ``EventThresholds.queue_spike_minutes`` the resolver produces."""
    rain_probability: float = 0.5
    rain_lookahead: timedelta = timedelta(hours=3)


DEFAULT_DETECTOR_CONFIG = DetectorConfig()


def detect_events(
    previous: LiveContext | None,
    current: LiveContext,
    *,
    now: datetime,
    reading_times: Mapping[str, datetime | None],
    config: DetectorConfig = DEFAULT_DETECTOR_CONFIG,
) -> list[Event]:
    """Candidate events between two consecutive snapshots, in a stable order.

    ``reading_times`` maps attraction id -> the provider's reading time in
    ``current`` (``None`` or absent: unknown). Empty when there is no previous
    snapshot to compare with, when the snapshots are out of order or more than
    ``max_gap`` apart, or when ``current`` is not fresh at ``now``.
    """
    _require_aware(now, "now")
    if previous is None or previous.retrieved_at >= current.retrieved_at:
        return []
    if current.retrieved_at - previous.retrieved_at > config.max_gap:
        return []
    if not _within_window(current.retrieved_at, now, config):
        return []

    def is_new(attraction_id: str) -> bool:
        observed = reading_times.get(attraction_id)
        return observed is not None and _within_window(observed, now, config)

    events = [
        *_status_events(previous, current, is_new),
        *_wait_spikes(previous, current, is_new, config),
        *_rain(previous, current, now, config),
    ]
    return sorted(events, key=_order)


def detect_behind_schedule(
    plan: Plan, execution_state: PlanExecutionState | None, *, now: datetime
) -> Event | None:
    """``BEHIND_SCHEDULE`` when a stop the party should have left is not done.

    The earliest stop whose planned departure is before ``now`` and that is
    neither completed nor skipped sets ``minutes_behind``. ``None`` without an
    execution state for this plan, or when the party is on schedule.
    """
    _require_aware(now, "now")
    if execution_state is None or execution_state.plan_id != plan.plan_id:
        return None
    done = {*execution_state.completed_stop_ids, *execution_state.skipped_stop_ids}
    overdue = [
        s for s in plan.stops if s.node_id not in done and s.departure_time < now
    ]
    if not overdue:
        return None
    late = min(overdue, key=lambda s: s.departure_time)
    behind = round((now - late.departure_time).total_seconds() / 60, 1)
    if behind <= 0:
        return None
    return Event(
        event_id=f"{EventType.BEHIND_SCHEDULE.value}:{plan.plan_id}:{late.node_id}",
        type=EventType.BEHIND_SCHEDULE,
        source=EventSource.MONITOR,
        attraction_id=late.node_id,
        confidence=_MONITOR_CONFIDENCE,
        timestamp=now,
        minutes_behind=behind,
    )


def _status_events(
    previous: LiveContext, current: LiveContext, is_new: IsNew
) -> list[Event]:
    events: list[Event] = []
    for attraction_id in sorted(current.statuses):
        before = previous.statuses.get(attraction_id)
        after = current.statuses[attraction_id]
        if before is None:
            continue
        if before is AttractionStatus.OPERATING and after in UNAVAILABLE:
            event_type = EventType.ATTRACTION_DOWN
        elif before in UNAVAILABLE and after is AttractionStatus.OPERATING:
            event_type = EventType.ATTRACTION_AVAILABLE
        else:
            continue
        if not is_new(attraction_id):
            continue
        events.append(
            Event(
                event_id=f"{event_type.value}:{attraction_id}:{current.snapshot_id}",
                type=event_type,
                source=EventSource.MONITOR,
                attraction_id=attraction_id,
                confidence=_MONITOR_CONFIDENCE,
                timestamp=current.retrieved_at,
            )
        )
    return events


def _wait_spikes(
    previous: LiveContext,
    current: LiveContext,
    is_new: IsNew,
    config: DetectorConfig,
) -> list[Event]:
    events: list[Event] = []
    for attraction_id in sorted(current.waits):
        after = current.waits[attraction_id]
        before = previous.waits.get(attraction_id)
        if before is None or not _both_operating(before.status, after.status):
            continue
        delta = after.wait_minutes - before.wait_minutes
        if delta < config.spike_floor_minutes or not is_new(attraction_id):
            continue
        events.append(
            Event(
                event_id=f"{EventType.WAIT_SPIKE.value}:{attraction_id}:{current.snapshot_id}",
                type=EventType.WAIT_SPIKE,
                source=EventSource.MONITOR,
                attraction_id=attraction_id,
                confidence=_MONITOR_CONFIDENCE,
                timestamp=current.retrieved_at,
                delta_minutes=delta,
            )
        )
    return events


def _rain(
    previous: LiveContext, current: LiveContext, now: datetime, config: DetectorConfig
) -> list[Event]:
    """Look-ahead hours that turned rainy. The weather is as fresh as its snapshot."""
    was_dry = {
        h.timestamp
        for h in previous.weather
        if h.precipitation_probability < config.rain_probability
    }
    horizon_end = now + config.rain_lookahead
    turned = sorted(
        (
            h
            for h in current.weather
            if h.precipitation_probability >= config.rain_probability
            and h.timestamp in was_dry
            and h.timestamp + timedelta(hours=1) > now  # the hour isn't over yet
            and h.timestamp < horizon_end
        ),
        key=lambda h: h.timestamp,
    )
    if not turned:
        return []
    hours = [h.timestamp for h in turned]
    return [
        Event(
            event_id=f"{EventType.RAIN_INCOMING.value}:{hours[0].isoformat()}:{current.snapshot_id}",
            type=EventType.RAIN_INCOMING,
            source=EventSource.MONITOR,
            confidence=max(h.precipitation_probability for h in turned),
            timestamp=current.retrieved_at,
            affected_hours=hours,
        )
    ]


def _within_window(at: datetime, now: datetime, config: DetectorConfig) -> bool:
    return now - config.freshness_window <= at <= now + config.clock_skew


def _order(event: Event) -> tuple[str, str, str]:
    first_hour = event.affected_hours[0].isoformat() if event.affected_hours else ""
    return (event.type.value, event.attraction_id or "", first_hour)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _both_operating(before: AttractionStatus, after: AttractionStatus) -> bool:
    return before is AttractionStatus.OPERATING and after is AttractionStatus.OPERATING
