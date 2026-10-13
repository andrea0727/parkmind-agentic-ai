"""Use case: say WHY a replan was proposed (section 23: a proposal states its reason).

The reason is built only from the events that required the replan, most severe first,
so the guests see "Space Mountain is down" and not an internal event type. The first
of them is the proposal's triggering event.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from parkmind.core.contracts import Event, EventSeverity, EventType
from parkmind.core.language import DEFAULT_LANGUAGE, Language, text
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    open_deps,
)

_SEVERITY_RANK = {EventSeverity.HIGH: 0, EventSeverity.MEDIUM: 1, EventSeverity.LOW: 2}


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


def _phrase(event: Event, names: Mapping[str, str], lang: Language) -> str:
    name = names.get(
        event.attraction_id or "",
        event.attraction_id or text("event_attraction_fallback", lang),
    )
    match event.type:
        case EventType.ATTRACTION_DOWN:
            return text("event_down", lang, name=name)
        case EventType.ATTRACTION_AVAILABLE:
            return text("event_available", lang, name=name)
        case EventType.WAIT_SPIKE:
            jump = (
                text("event_wait_spike_by", lang, minutes=f"{event.delta_minutes:.0f}")
                if event.delta_minutes is not None
                else ""
            )
            return text("event_wait_spike", lang, name=name, jump=jump)
        case EventType.RAIN_INCOMING:
            return text("event_rain", lang)
        case EventType.BEHIND_SCHEDULE:
            late = (
                text("event_behind_by", lang, minutes=f"{event.minutes_behind:.0f}")
                if event.minutes_behind is not None
                else ""
            )
            return text("event_behind", lang, late=late)
        case EventType.GUEST_FATIGUE:
            return text("event_fatigue", lang)
        case _:
            return event.type.value.replace("_", " ").lower()


def replan_reason(
    events: Sequence[Event], names: Mapping[str, str], lang: Language = DEFAULT_LANGUAGE
) -> ReplanReason:
    ranked = _ranked(events)
    if not ranked:
        return ReplanReason(text("reason_none", lang), None)
    phrases: list[str] = []
    for event in ranked:
        phrase = _phrase(event, names, lang)
        if phrase not in phrases:
            phrases.append(phrase)
    joined = text("reason_and", lang).join(phrases)
    return ReplanReason(text("reason_prefix", lang, phrases=joined), ranked[0].event_id)


class ReplanReasonUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(
        self, events: Sequence[Event], lang: Language = DEFAULT_LANGUAGE
    ) -> ReplanReason:
        with open_deps(self._deps_factory) as deps:
            names = {a.node_id: a.name for a in load_catalog(deps)}
        return replan_reason(events, names, lang)
