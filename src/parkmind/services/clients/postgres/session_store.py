"""SessionStore honoring ``retention_policy`` (section 12, 33 [C19]).

* ``session_only`` records live in this object's memory, keyed by
  ``(session_id, guest_id)``. They never reach the connection, so they cannot
  land in any table -- checkpoint tables included.
* ``persisted`` records require consent and are stored as derived flags only in
  ``accessibility_requirements`` (which a CHECK constraint restricts to exactly
  such rows).

Limitation: the session memory is process-local. A deployment with several API
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


class PostgresSessionStore(PostgresRepositoryBase):
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        super().__init__(conn)
        self._lock = threading.Lock()
        self._session_records: dict[str, dict[str, AccessibilityRequirements]] = {}

    def put(self, session_id: str, requirements: AccessibilityRequirements) -> None:
        if requirements.retention_policy == "session_only":
            with self._lock:
                self._session_records.setdefault(session_id, {})[
                    requirements.guest_id
                ] = requirements
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
        with self._lock:
            held = self._session_records.get(session_id, {}).get(guest_id)
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
        with self._lock:
            self._session_records.pop(session_id, None)
