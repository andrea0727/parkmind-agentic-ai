"""ProfileRepository -- versioned ``GuestProfile`` history (section 33 [C12, C15]).

Profiles are versioned and never overwritten: every ``save`` appends a
``profile_version``. Merge/update semantics (which dimensions change, that
``stated_value`` survives) belong to the GuestProfile service, P0-14.
"""

from typing import Protocol

from parkmind.core.contracts import GuestProfile


class ProfileRepository(Protocol):
    def save(self, profile: GuestProfile) -> None:
        """Append ``profile`` as a new version.

        Raises ``NotFoundError`` for an unknown guest and
        ``ProfileVersionConflictError`` unless ``profile_version`` is greater
        than the latest stored one. Re-saving an identical latest version is a
        no-op so retries are safe.
        """
        ...

    def get_latest(self, guest_id: str) -> GuestProfile | None: ...

    def get_version(self, guest_id: str, profile_version: int) -> GuestProfile | None: ...
