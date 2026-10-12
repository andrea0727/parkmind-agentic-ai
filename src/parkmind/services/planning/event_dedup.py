"""Cross-source event identity (sections 24 and 43 "Duplicate event", backlog P0-33).

"The monitor and a user report of the same closure produce **one** event:
idempotency keys cover both sources." ``deduplicate`` gives every candidate an
``event_id`` that does not depend on who reported it, and drops the ones the
party's thread already holds. ``EventRepository.record`` is idempotent on
that id, which also makes a re-run of the monitor harmless.

Two kinds of identity:

* **Episodes** -- ``ATTRACTION_DOWN`` / ``ATTRACTION_AVAILABLE``. An attraction
  is down until it is available again. While the thread's latest of the two for
  that attraction has the candidate's type, the episode is still open and the
  candidate is a duplicate -- with one exception: a **monitor** candidate more
  than ``window`` after that latest event opens a new episode. The monitor only
  reports a transition it saw (OPERATING -> DOWN), so the attraction was up in
  between: the open episode was a wrong report or its closing event was missed,
  and absorbing the candidate would hide a real closure for good. Within the
  window it is the same closure, whichever source spoke first; a late user
  report of a closure already held stays a duplicate. A new episode gets
  ``"{TYPE}:{attraction}:{n}"``, ``n`` counting that attraction's events of the
  type, so DOWN -> AVAILABLE -> DOWN is three events.
* **Moments** -- every other type. The same (type, subject) within ``window`` of
  one already held is a duplicate; otherwise the id is
  ``"{TYPE}:{subject}:{UTC minute}"``. Subjects: the attraction (wait spike),
  the first affected hour (rain), the overdue stop (behind schedule), the guest
  (fatigue), the location (relocation).

Pure: the thread's history is passed in, ``now`` is never read.
"""

from collections.abc import Iterable
from datetime import UTC, timedelta

from parkmind.core.contracts import Event, EventSource, EventType
from parkmind.services.planning.event_detector import FRESHNESS_WINDOW

EPISODIC = frozenset({EventType.ATTRACTION_DOWN, EventType.ATTRACTION_AVAILABLE})


def deduplicate(
    candidates: Iterable[Event],
    history: Iterable[Event],
    *,
    window: timedelta = FRESHNESS_WINDOW,
) -> list[Event]:
    """The candidates that are new to the thread, each with its cross-source id.

    ``history`` is the thread's recorded events. Candidates are taken in
    (timestamp, type, subject) order, and each kept one counts as history for
    the next, so two duplicates in one batch keep one.
    """
    seen = sorted(history, key=lambda e: (e.timestamp, e.event_id))
    kept: list[Event] = []
    for candidate in sorted(
        candidates, key=lambda e: (e.timestamp, e.type.value, subject(e))
    ):
        if candidate.type in EPISODIC:
            event_id = _episode_id(candidate, seen, window)
        else:
            event_id = _moment_id(candidate, seen, window)
        if event_id is None:
            continue
        identified = candidate.model_copy(update={"event_id": event_id})
        kept.append(identified)
        seen.append(identified)
    return kept


def subject(event: Event) -> str:
    """What an event is about, for identity."""
    match event.type:
        case EventType.RAIN_INCOMING:
            hours = event.affected_hours or []
            return hours[0].astimezone(UTC).strftime("%Y%m%dT%H%MZ") if hours else ""
        case EventType.GUEST_FATIGUE:
            return event.guest_id or ""
        case EventType.PARTY_RELOCATED:
            return event.location_node_id or ""
        case _:
            return event.attraction_id or ""


def _episode_id(candidate: Event, seen: list[Event], window: timedelta) -> str | None:
    attraction = candidate.attraction_id
    same_attraction = [
        e for e in seen if e.type in EPISODIC and e.attraction_id == attraction
    ]
    if same_attraction and same_attraction[-1].type is candidate.type:
        latest = same_attraction[-1]
        seen_again = (
            candidate.source is EventSource.MONITOR
            and candidate.timestamp - latest.timestamp > window
        )
        if not seen_again:
            return None  # the episode is still open
    n = 1 + sum(1 for e in same_attraction if e.type is candidate.type)
    return f"{candidate.type.value}:{attraction}:{n}"


def _moment_id(candidate: Event, seen: list[Event], window: timedelta) -> str | None:
    key = subject(candidate)
    for earlier in seen:
        if (
            earlier.type is candidate.type
            and subject(earlier) == key
            and abs(candidate.timestamp - earlier.timestamp) <= window
        ):
            return None
    minute = candidate.timestamp.astimezone(UTC).strftime("%Y%m%dT%H%MZ")
    return f"{candidate.type.value}:{key}:{minute}"
