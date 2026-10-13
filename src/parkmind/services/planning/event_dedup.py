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
  candidate is a duplicate -- with one exception: a **monitor** candidate long
  after that latest event opens a new episode. The monitor only reports a
  transition it saw (OPERATING -> DOWN), so the attraction was up in between:
  the open episode was a wrong report or its closing event was missed, and
  absorbing the candidate would hide a real closure for good. "Long" is
  ``window`` after a monitor event, and ``user_episode_window`` (2 h, a
  documented starting point) after a user report: the provider can take a
  while to show a closure a guest saw first, and that lag is the same closure,
  not a second one. A late user report of a closure already held stays a
  duplicate. A new episode gets
  ``"{TYPE}:{attraction}:{n}"``, ``n`` counting that attraction's events of the
  type, so DOWN -> AVAILABLE -> DOWN is three events.
* **Once per overdue stop** -- ``BEHIND_SCHEDULE``. The subject is the overdue
  stop and its planned departure (``timestamp - minutes_behind``), and the
  thread holds it once, however long the party stays behind: every repeat would
  supersede the replan proposal the first one asked for. An approved replan
  moves the departures, so falling behind on the new plan is a new event. The
  detector only emits it from the policy's threshold, so the one event held is
  always the crossing that replans, never a small delay. The id is
  ``"BEHIND_SCHEDULE:{stop}@{departure}"``.
* **Moments** -- every other type. The same (type, subject) within ``window`` of
  one already held is a duplicate; otherwise the id is
  ``"{TYPE}:{subject}:{UTC minute}"``. Subjects: the attraction (wait spike),
  the first affected hour (rain), the guest (fatigue), the location
  (relocation).

Pure: the thread's history is passed in, ``now`` is never read.
"""

from collections.abc import Iterable
from datetime import UTC, timedelta

from parkmind.core.contracts import Event, EventSource, EventType
from parkmind.services.planning.event_detector import FRESHNESS_WINDOW

EPISODIC = frozenset({EventType.ATTRACTION_DOWN, EventType.ATTRACTION_AVAILABLE})

USER_EPISODE_WINDOW = timedelta(hours=2)
"""How long the provider may lag behind a guest's report of the same closure."""


def deduplicate(
    candidates: Iterable[Event],
    history: Iterable[Event],
    *,
    window: timedelta = FRESHNESS_WINDOW,
    user_episode_window: timedelta = USER_EPISODE_WINDOW,
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
            event_id = _episode_id(candidate, seen, window, user_episode_window)
        elif candidate.type is EventType.BEHIND_SCHEDULE:
            event_id = _once_id(candidate, seen)
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
        case EventType.BEHIND_SCHEDULE:
            return f"{event.attraction_id or ''}@{_planned_departure(event)}"
        case _:
            return event.attraction_id or ""


def _planned_departure(event: Event) -> str:
    if event.minutes_behind is None:
        return ""
    at = event.timestamp - timedelta(minutes=event.minutes_behind)
    to_the_second = (at + timedelta(milliseconds=500)).replace(microsecond=0)
    return to_the_second.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _episode_id(
    candidate: Event,
    seen: list[Event],
    window: timedelta,
    user_episode_window: timedelta,
) -> str | None:
    attraction = candidate.attraction_id
    same_attraction = [
        e for e in seen if e.type in EPISODIC and e.attraction_id == attraction
    ]
    if same_attraction and same_attraction[-1].type is candidate.type:
        latest = same_attraction[-1]
        open_for = user_episode_window if latest.source is EventSource.USER else window
        seen_again = (
            candidate.source is EventSource.MONITOR
            and candidate.timestamp - latest.timestamp > open_for
        )
        if not seen_again:
            return None  # the episode is still open
    n = 1 + sum(1 for e in same_attraction if e.type is candidate.type)
    return f"{candidate.type.value}:{attraction}:{n}"


def _once_id(candidate: Event, seen: list[Event]) -> str | None:
    key = subject(candidate)
    if any(e.type is candidate.type and subject(e) == key for e in seen):
        return None
    return f"{candidate.type.value}:{key}"


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
