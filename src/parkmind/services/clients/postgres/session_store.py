"""SessionStore honoring ``retention_policy`` (section 12, 33 [C19]).

* ``session_only`` records live in a ``SessionMemory``, keyed by
  ``(session_id, guest_id)``. They never reach a connection, so they cannot
  land in any table -- checkpoint tables included.
* ``persisted`` records require consent and are stored as derived flags only in
  ``accessibility_requirements`` (which a CHECK constraint restricts to exactly
  such rows).

Lifetimes are deliberately split. The store, like every repository, is built
on the caller's connection and may be short-lived (one per request). The
``SessionMemory`` must outlive it: create **one per process** at startup and
pass the same instance to every store. It is a required argument so that a
fresh, empty memory per request -- which would silently drop a guest's
accessibility needs mid-session -- cannot happen by default.

Limitation: the memory is process-local. A deployment with several API
workers needs sticky sessions or another non-table store; that is an open
question for P0-35, deliberately not decided here.
"""

import threading
from typing import Any

import psycopg

from parkmind.core.contracts import AccessibilityRequirements
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase
from parkmind.services.ports.errors import ConsentRequiredError


class SessionMemory:
    """Process-scoped holder of ``session_only`` records. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, AccessibilityRequirements]] = {}

    def put(self, session_id: str, requirements: AccessibilityRequirements) -> None:
        with self._lock:
            self._records.setdefault(session_id, {})[requirements.guest_id] = requirements

    def get(self, session_id: str, guest_id: str) -> AccessibilityRequirements | None:
        with self._lock:
            return self._records.get(session_id, {}).get(guest_id)

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._records.pop(session_id, None)


class PostgresSessionStore(PostgresRepositoryBase):
    def __init__(self, conn: psycopg.Connection[Any], memory: SessionMemory) -> None:
        super().__init__(conn)
        self._memory = memory

    def put(self, session_id: str, requirements: AccessibilityRequirements) -> None:
        if requirements.retention_policy == "session_only":
            self._memory.put(session_id, requirements)
            return

        if not requirements.consent:
            raise ConsentRequiredError("persisting accessibility data requires explicit consent")
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO accessibility_requirements
                    (guest_id, retention_policy, consent, payload)
                VALUES (%s, 'persisted', true, %s)
                ON CONFLICT (guest_id) DO UPDATE
                    SET payload = EXCLUDED.payload, stored_at = now()
                """,
                (requirements.guest_id, to_jsonb(requirements)),
            )

    def get(self, session_id: str, guest_id: str) -> AccessibilityRequirements | None:
        held = self._memory.get(session_id, guest_id)
        if held is not None:
            return held

        with self._tx() as cur:
            cur.execute(
                "SELECT payload FROM accessibility_requirements WHERE guest_id = %s",
                (guest_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return from_payload(AccessibilityRequirements, row["payload"], what="accessibility record")

    def end_session(self, session_id: str) -> None:
        self._memory.drop(session_id)
