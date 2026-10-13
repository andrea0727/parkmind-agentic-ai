"""EventPolicy -- severity, ``requires_replan`` and a bounded effect per event
(sections 24-25 [C10, C23], backlog P0-33).

The single deterministic judge for both sources: a monitor event and a
user-reported one go through the same table, and **this is the only module that
sets ``Event.severity`` or ``Event.requires_replan``** (tests/architecture
guards it). The LLM's confidence feeds the confirmation gate only; it never
sets ``requires_replan``.

Section 25's table, with "in plan" read as *a stop the party has not done
yet* (completed and skipped stops are history):

=====================  =======================================  ==================================
Type                   Effect                                   Severity
=====================  =======================================  ==================================
ATTRACTION_DOWN        ``ForbidNode``                           HIGH if a remaining stop, or a
                                                                must-do not done and not already
                                                                unmet; else LOW
ATTRACTION_AVAILABLE   ``LiftForbid``                           MEDIUM if in ``unmet_must_do``,
                                                                else LOW (no replan) [C23]
WAIT_SPIKE             ``Reforecast``                           MEDIUM if ``delta_minutes`` >=
                                                                ``event_thresholds.queue_spike``
                                                                and a remaining stop; else LOW
RAIN_INCOMING          ``PenalizeOutdoor`` (hours, stops)       MEDIUM if a remaining outdoor stop
                                                                overlaps an affected hour
BEHIND_SCHEDULE        ``ReplanFromAsOf``                       MEDIUM if ``minutes_behind`` >=
                                                                ``behind_schedule_minutes``
GUEST_FATIGUE          ``CapWalking`` + ``ForceRest``           MEDIUM
PARTY_RELOCATED        ``Relocated`` (applied before policy)    LOW, never a trigger [C23]
=====================  =======================================  ==================================

``requires_replan`` is ``severity >= MEDIUM``, except ``PARTY_RELOCATED``.

Two values are not in section 25 and are documented starting points, to
calibrate like the resolver's threshold ranges:

* ``behind_schedule_minutes`` = 15 -- ``EventThresholds`` has no such field;
  section 39.1's +18 minutes is MEDIUM.
* the fatigue cap (decided 2026-10-12): ``min(daily limit - walked,
  fatigue_cap_fraction x walking left in the plan)`` with fraction 0.5; only the
  plan term without a daily limit (or when the guest's requirements could not
  be read -- that report then needs confirmation); never above an existing cap,
  never below 0.
  Section 33: the cap "never exceeds daily limit - consumed". The daily limit is
  passed in from the guest's ``AccessibilityRequirements``; it never comes
  from, or goes to, graph state [C19].

Effects are typed and bounded -- never free-form edits (section 25 [C23]).
``PARTY_RELOCATED`` and the walking cap touch ``PlanExecutionState``
(``apply_execution_updates`` before the policy, ``apply_decisions`` after);
the others are for the replanner (P0-34 passes them on).
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from parkmind.core.contracts import (
    Event,
    EventSeverity,
    EventSource,
    EventThresholds,
    EventType,
    Plan,
    PlanExecutionState,
    Stop,
)


class UnconfirmedEventError(ValueError):
    """A user event that needs the confirmation interrupt reached the policy unconfirmed."""


@dataclass(frozen=True)
class PolicyConfig:
    behind_schedule_minutes: float = 15.0
    min_user_confidence: float = 0.7
    """Below this, a user-reported event is confirmed before the policy runs (section 25 [C10])."""
    fatigue_cap_fraction: float = 0.5


DEFAULT_POLICY_CONFIG = PolicyConfig()


@dataclass(frozen=True)
class PartyEventContext:
    """What the policy needs to know about one party (the replanning graph's state)."""

    plan: Plan
    """The party's active plan."""
    must_do: Collection[str]
    thresholds: EventThresholds
    outdoor_ids: Collection[str]
    """Attractions the catalog marks ``outdoor``."""
    execution_state: PlanExecutionState | None = None
    daily_walking_limits: Mapping[str, int] | None = None
    """guest_id -> daily walking limit, from AccessibilityRequirements; never from state [C19]."""
    unread_requirements: Collection[str] = ()
    """Guests whose AccessibilityRequirements could not be read: their limit is unknown."""


# ---- effects ---------------------------------------------------------------


@dataclass(frozen=True)
class ForbidNode:
    attraction_id: str


@dataclass(frozen=True)
class LiftForbid:
    attraction_id: str


@dataclass(frozen=True)
class Reforecast:
    attraction_id: str


@dataclass(frozen=True)
class PenalizeOutdoor:
    hours: tuple[datetime, ...]
    attraction_ids: tuple[str, ...]
    """The remaining outdoor stops that fall in those hours."""


@dataclass(frozen=True)
class ReplanFromAsOf:
    minutes_behind: float


@dataclass(frozen=True)
class CapWalking:
    guest_id: str
    minutes: float


@dataclass(frozen=True)
class ForceRest:
    guest_id: str


@dataclass(frozen=True)
class Relocated:
    location_node_id: str


Effect = (
    ForbidNode
    | LiftForbid
    | Reforecast
    | PenalizeOutdoor
    | ReplanFromAsOf
    | CapWalking
    | ForceRest
    | Relocated
)


@dataclass(frozen=True)
class PolicyDecision:
    event: Event
    """The event with ``severity`` and ``requires_replan`` set."""
    effects: tuple[Effect, ...]


# ---- confirmation gate -----------------------------------------------------


def needs_confirmation(
    event: Event,
    *,
    thresholds: EventThresholds,
    daily_walking_limits: Mapping[str, int] | None = None,
    unread_requirements: Collection[str] = (),
    config: PolicyConfig = DEFAULT_POLICY_CONFIG,
) -> bool:
    """Section 25 [C10]: does this event need the guests' confirmation first?

    Only user reports do: when their confidence is below
    ``min_user_confidence``, or for ``GUEST_FATIGUE`` -- hard-constraint
    adjacent -- when the guest has a daily walking limit, when their
    requirements could not be read (the limit is unknown, so it fails closed as
    if there were one), or when the confidence is below
    ``thresholds.fatigue_threshold``.
    """
    if event.source is not EventSource.USER:
        return False
    if event.confidence < config.min_user_confidence:
        return True
    if event.type is EventType.GUEST_FATIGUE:
        has_limit = event.guest_id in (daily_walking_limits or {})
        unknown = event.guest_id in unread_requirements
        return has_limit or unknown or event.confidence < thresholds.fatigue_threshold
    return False


# ---- execution-state updates -----------------------------------------------


def apply_execution_updates(
    state: PlanExecutionState | None,
    events: Iterable[Event],
    *,
    plan_id: str,
    now: datetime,
) -> PlanExecutionState | None:
    """Apply ``PARTY_RELOCATED`` to the execution state, before the policy (section 36).

    The latest report wins. ``as_of`` moves forward to it, never back. Without
    a relocation the state comes back unchanged (``None`` stays ``None``).
    """
    _require_aware(now, "now")
    moves = sorted(
        (e for e in events if e.type is EventType.PARTY_RELOCATED),
        key=lambda e: (e.timestamp, e.event_id),
    )
    if not moves:
        return state
    latest = moves[-1]
    base = state or PlanExecutionState(plan_id=plan_id, as_of=latest.timestamp)
    return base.model_copy(
        update={
            "current_location_node_id": latest.location_node_id,
            "as_of": max(base.as_of, latest.timestamp),
        }
    )


def apply_decisions(
    state: PlanExecutionState | None,
    decisions: Iterable[PolicyDecision],
    *,
    plan_id: str,
    now: datetime,
) -> PlanExecutionState | None:
    """Write each ``CapWalking`` into ``remaining_walking_cap_minutes``."""
    _require_aware(now, "now")
    caps = [e for d in decisions for e in d.effects if isinstance(e, CapWalking)]
    if not caps:
        return state
    base = state or PlanExecutionState(plan_id=plan_id, as_of=now)
    remaining = dict(base.remaining_walking_cap_minutes)
    for cap in caps:
        remaining[cap.guest_id] = min(
            remaining.get(cap.guest_id, cap.minutes), cap.minutes
        )
    return base.model_copy(update={"remaining_walking_cap_minutes": remaining})


# ---- the table -------------------------------------------------------------


def classify(
    events: Sequence[Event],
    *,
    party: PartyEventContext,
    confirmed: bool,
    config: PolicyConfig = DEFAULT_POLICY_CONFIG,
) -> list[PolicyDecision]:
    """Severity, ``requires_replan`` and effects for each event, in input order.

    Raises ``UnconfirmedEventError`` if an event that ``needs_confirmation`` is
    classified without ``confirmed``.
    """
    decisions: list[PolicyDecision] = []
    for event in events:
        gate = needs_confirmation(
            event,
            thresholds=party.thresholds,
            daily_walking_limits=party.daily_walking_limits,
            unread_requirements=party.unread_requirements,
            config=config,
        )
        if gate and not confirmed:
            raise UnconfirmedEventError(
                f"{event.type.value} {event.event_id} needs the guests' confirmation first"
            )
        severity, effects = _judge(event, party, config)
        requires_replan = event.type is not EventType.PARTY_RELOCATED and severity in (
            EventSeverity.MEDIUM,
            EventSeverity.HIGH,
        )
        decisions.append(
            PolicyDecision(
                event=event.model_copy(
                    update={"severity": severity, "requires_replan": requires_replan}
                ),
                effects=effects,
            )
        )
    return decisions


def _judge(
    event: Event, party: PartyEventContext, config: PolicyConfig
) -> tuple[EventSeverity, tuple[Effect, ...]]:
    remaining = _remaining_stops(party)
    remaining_ids = {s.node_id for s in remaining}
    attraction = event.attraction_id or ""
    low, medium, high = EventSeverity.LOW, EventSeverity.MEDIUM, EventSeverity.HIGH

    match event.type:
        case EventType.ATTRACTION_DOWN:
            done = _done(party)
            pending_must_do = (
                attraction in party.must_do
                and attraction not in done
                and attraction not in party.plan.unmet_must_do
            )
            significant = attraction in remaining_ids or pending_must_do
            return (high if significant else low), (ForbidNode(attraction),)
        case EventType.ATTRACTION_AVAILABLE:
            significant = attraction in party.plan.unmet_must_do
            return (medium if significant else low), (LiftForbid(attraction),)
        case EventType.WAIT_SPIKE:
            delta = event.delta_minutes or 0.0
            significant = (
                delta >= party.thresholds.queue_spike_minutes
                and attraction in remaining_ids
            )
            return (medium if significant else low), (Reforecast(attraction),)
        case EventType.RAIN_INCOMING:
            hours = tuple(event.affected_hours or ())
            wet = tuple(
                s.node_id
                for s in remaining
                if s.node_id in party.outdoor_ids and _overlaps(s, hours)
            )
            return (medium if wet else low), (PenalizeOutdoor(hours, wet),)
        case EventType.BEHIND_SCHEDULE:
            behind = event.minutes_behind or 0.0
            significant = behind >= config.behind_schedule_minutes
            return (medium if significant else low), (ReplanFromAsOf(behind),)
        case EventType.GUEST_FATIGUE:
            guest = event.guest_id or ""
            cap = _fatigue_cap(guest, remaining, party, config)
            return medium, (CapWalking(guest, cap), ForceRest(guest))
        case EventType.PARTY_RELOCATED:
            return low, (Relocated(event.location_node_id or ""),)
    raise ValueError(f"no policy for event type {event.type!r}")  # pragma: no cover


def _fatigue_cap(
    guest_id: str,
    remaining: Sequence[Stop],
    party: PartyEventContext,
    config: PolicyConfig,
) -> float:
    plan_term = config.fatigue_cap_fraction * sum(s.walking_minutes for s in remaining)
    cap = plan_term
    limit = (party.daily_walking_limits or {}).get(guest_id)
    state = party.execution_state
    if limit is not None:
        consumed = state.walking_minutes_consumed.get(guest_id, 0.0) if state else 0.0
        cap = min(limit - consumed, plan_term)
    if state is not None and guest_id in state.remaining_walking_cap_minutes:
        cap = min(cap, state.remaining_walking_cap_minutes[guest_id])
    return round(max(cap, 0.0), 1)


def _done(party: PartyEventContext) -> set[str]:
    state = party.execution_state
    if state is None:
        return set()
    return {*state.completed_stop_ids, *state.skipped_stop_ids}


def _remaining_stops(party: PartyEventContext) -> list[Stop]:
    done = _done(party)
    return [s for s in party.plan.stops if s.node_id not in done]


def _overlaps(stop: Stop, hours: Sequence[datetime]) -> bool:
    return any(
        stop.arrival_time < hour + timedelta(hours=1) and hour < stop.departure_time
        for hour in hours
    )


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
