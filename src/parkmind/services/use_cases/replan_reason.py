"""Use case: say WHY a replan was proposed (section 23: a proposal states its reason).

The reason is built only from the events that required the replan, most severe first,
so the guests see "Space Mountain is down" and not an internal event type. The first
of them is the proposal's triggering event.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from parkmind.core.contracts import Event, EventSeverity, EventType
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    open_deps,
)

_SEVERITY_RANK = {EventSeverity.HIGH: 0, EventSeverity.MEDIUM: 1, EventSeverity.LOW: 2}
_NO_REASON = "The plan was updated after a change in the park."


@dataclass(frozen=True)
class ReplanReason:
    text: str
    triggering_event_id: str | None


def _ranked(events: Sequence[Event]) -> list[Event]:
    triggers = [e for e in events if e.requires_replan]
    return sorted(
        triggers,
        key=lambda e: (
            _SEVERITY_RANK.get(e.severity or EventSeverity.LOW, 2),
            e.timestamp,
            e.event_id,
        ),
    )


def _phrase(event: Event, names: Mapping[str, str]) -> str:
    name = names.get(event.attraction_id or "", event.attraction_id or "an attraction")
    match event.type:
        case EventType.ATTRACTION_DOWN:
            return f"{name} is down"
        case EventType.ATTRACTION_AVAILABLE:
            return f"{name} is available again"
        case EventType.WAIT_SPIKE:
            jump = (
                f" by {event.delta_minutes:.0f} min"
                if event.delta_minutes is not None
                else ""
            )
            return f"the wait at {name} jumped{jump}"
        case EventType.RAIN_INCOMING:
            return "rain is coming"
        case EventType.BEHIND_SCHEDULE:
            late = (
                f" {event.minutes_behind:.0f} min"
                if event.minutes_behind is not None
                else ""
            )
            return f"the party is{late} behind schedule"
        case EventType.GUEST_FATIGUE:
            return "a guest is tired"
        case _:
            return event.type.value.replace("_", " ").lower()


def replan_reason(events: Sequence[Event], names: Mapping[str, str]) -> ReplanReason:
    ranked = _ranked(events)
    if not ranked:
        return ReplanReason(_NO_REASON, None)
    phrases: list[str] = []
    for event in ranked:
        phrase = _phrase(event, names)
        if phrase not in phrases:
            phrases.append(phrase)
    text = "Updated plan because " + " and ".join(phrases) + "."
    return ReplanReason(text, ranked[0].event_id)


class ReplanReasonUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(self, events: Sequence[Event]) -> ReplanReason:
        with open_deps(self._deps_factory) as deps:
            names = {a.node_id: a.name for a in load_catalog(deps)}
        return replan_reason(events, names)
