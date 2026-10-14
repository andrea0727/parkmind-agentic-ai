"""BehaviorLogRepository -- the separate ``BehaviorLog`` aggregate (section 33 [C12]).

The log is keyed by guest. A party's guest ids (``g1``, ``g2``) are only unique
within its thread, so every writer keys a party's guest as ``party_guest_id``.
"""

from typing import Protocol

from parkmind.core.contracts import BehaviorEntry, BehaviorLog


def party_guest_id(thread_id: str, guest_id: str) -> str:
    """The guest's id in the behavior log: unique across parties."""
    return f"{thread_id}:{guest_id}"


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
