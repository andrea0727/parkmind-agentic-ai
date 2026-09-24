"""GuestRepository -- identity facts only (section 33 ``Guest`` [C15])."""

from typing import Protocol

from parkmind.core.contracts import Guest


class GuestRepository(Protocol):
    def save(self, guest: Guest) -> None:
        """Insert or update the guest (upsert on ``guest_id``)."""
        ...

    def get(self, guest_id: str) -> Guest | None: ...

    def list_all(self) -> list[Guest]:
        """All guests ordered by ``guest_id``."""
        ...
