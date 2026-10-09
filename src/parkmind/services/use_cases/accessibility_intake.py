"""Use case: take accessibility statements from extraction to the SessionStore [C15, C19].

Statements extracted by the LLM are only *staged* here, in process memory that
is neither graph state nor a table. They reach the ``SessionStore`` -- and so
the ConstraintChecker -- only through ``commit``, which the confirmation step of
the graph calls after the human has confirmed the echoed constraint and given
consent. Consent and retention come from that human decision, never from the
LLM.

Only derived flags are staged; the underlying statement is never kept.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Literal

from parkmind.core.contracts import (
    AccessibilityRequirements,
    MobilityRequirement,
    RideRestriction,
)
from parkmind.services.clients.postgres import PostgresSessionStore, connect
from parkmind.services.ports import ConsentRequiredError, SessionStore
from parkmind.services.use_cases.session_memory import SESSION_MEMORY

RetentionPolicy = Literal["session_only", "persisted"]
DEFAULT_RETENTION: RetentionPolicy = "session_only"


@dataclass(frozen=True)
class StagedAccessibility:
    """Derived accessibility flags awaiting confirmation. Carries no consent."""

    guest_id: str
    daily_walking_limit_minutes: int | None = None
    rest_frequency_minutes: int | None = None
    mobility_requirements: tuple[MobilityRequirement, ...] = ()
    heat_sensitivity: bool = False
    ride_restrictions: tuple[RideRestriction, ...] = field(default_factory=tuple)

    def has_flags(self) -> bool:
        return bool(
            self.daily_walking_limit_minutes is not None
            or self.rest_frequency_minutes is not None
            or self.mobility_requirements
            or self.heat_sensitivity
            or self.ride_restrictions
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.daily_walking_limit_minutes is not None:
            parts.append(
                f"a {self.daily_walking_limit_minutes}-minute daily walking limit"
            )
        if self.rest_frequency_minutes is not None:
            parts.append(f"a rest break every {self.rest_frequency_minutes} minutes")
        parts.extend(
            m.value.replace("_", " ").lower() for m in self.mobility_requirements
        )
        if self.heat_sensitivity:
            parts.append("heat sensitivity")
        parts.extend(r.value.replace("_", " ").lower() for r in self.ride_restrictions)
        return ", ".join(parts)


@contextmanager
def _default_store() -> Iterator[SessionStore]:
    conn = connect()
    try:
        yield PostgresSessionStore(conn, SESSION_MEMORY)
    finally:
        conn.close()


class AccessibilityIntakeUseCase:
    """Stage unconfirmed accessibility flags; commit them only once confirmed."""

    def __init__(
        self,
        store_factory: Callable[
            [], AbstractContextManager[SessionStore]
        ] = _default_store,
    ) -> None:
        self._store_factory = store_factory
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, StagedAccessibility]] = {}

    def stage(self, session_id: str, staged: StagedAccessibility) -> None:
        if not staged.has_flags():
            return
        with self._lock:
            self._pending.setdefault(session_id, {})[staged.guest_id] = staged

    def pending_guest_ids(self, session_id: str) -> list[str]:
        with self._lock:
            return sorted(self._pending.get(session_id, {}))

    def describe(self, session_id: str) -> dict[str, str]:
        """Echo text per guest id, for the confirmation UI.

        Read from here, not from the interrupt payload: the payload is
        checkpointed and must not carry accessibility flags [C19].
        """
        with self._lock:
            return {
                guest_id: staged.describe()
                for guest_id, staged in sorted(
                    self._pending.get(session_id, {}).items()
                )
            }

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._pending.pop(session_id, None)

    def commit(
        self,
        session_id: str,
        *,
        consent: bool,
        retention_policy: RetentionPolicy = DEFAULT_RETENTION,
    ) -> list[str]:
        """Move every staged record into the SessionStore; return the guest ids.

        Raises ``ConsentRequiredError`` without consent, before anything is
        written or removed from staging.
        """
        with self._lock:
            staged = dict(self._pending.get(session_id, {}))
        if not staged:
            return []
        if not consent:
            raise ConsentRequiredError(
                "accessibility data can only be stored with explicit consent"
            )

        with self._store_factory() as store:
            for guest_id, item in staged.items():
                store.put(
                    session_id,
                    AccessibilityRequirements(
                        guest_id=guest_id,
                        daily_walking_limit_minutes=item.daily_walking_limit_minutes,
                        rest_frequency_minutes=item.rest_frequency_minutes,
                        mobility_requirements=list(item.mobility_requirements),
                        heat_sensitivity=item.heat_sensitivity,
                        ride_restrictions=list(item.ride_restrictions),
                        consent=consent,
                        retention_policy=retention_policy,
                    ),
                )
        self.discard(session_id)
        return sorted(staged)
