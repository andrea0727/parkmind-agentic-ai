"""EventRepository -- typed events from monitor and user (section 33 ``Event`` [C10]).

The repository stores whatever ``severity`` / ``requires_replan`` it is given;
those are set by EventPolicy only, upstream of this port.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from parkmind.core.contracts import Event


class EventRepository(Protocol):
    def record(self, thread_id: str, event: Event) -> bool:
        """Record ``event`` for the thread.

        ``event_id`` is the idempotency key across sources (section 43
        "Duplicate event"): returns ``False`` and stores nothing when the thread
        already recorded it.
        """
        ...

    def forget(self, thread_id: str, event_ids: Sequence[str]) -> None:
        """Drop the thread's events with these ids (none of them need exist).

        For an event whose replan could not start: it was recorded, so a report of
        the same thing would be taken for a duplicate and never replanned.
        """
        ...

    def list_for_thread(
        self, thread_id: str, *, since: datetime | None = None
    ) -> list[Event]:
        """The thread's events ordered by timestamp, then ``event_id``.

        ``since`` is inclusive (events at or after it): recording is idempotent,
        so re-reading a boundary event is harmless while skipping one is not.
        """
        ...
