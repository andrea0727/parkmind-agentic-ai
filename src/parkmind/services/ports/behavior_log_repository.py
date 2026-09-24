"""BehaviorLogRepository -- the separate ``BehaviorLog`` aggregate (section 33 [C12])."""

from typing import Protocol

from parkmind.core.contracts import BehaviorEntry, BehaviorLog


class BehaviorLogRepository(Protocol):
    def append(self, guest_id: str, entry: BehaviorEntry) -> bool:
        """Record ``entry`` for the guest.

        ``entry_id`` is the idempotency key: returns ``False`` (and stores
        nothing) when that entry is already logged for the guest. Raises
        ``NotFoundError`` for an unknown guest.
        """
        ...

    def get(self, guest_id: str) -> BehaviorLog:
        """The guest's log, oldest first; empty when nothing was recorded."""
        ...
