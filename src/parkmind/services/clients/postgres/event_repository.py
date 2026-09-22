"""PostgreSQL adapter for ``EventRepository`` (idempotent on ``event_id`` per thread)."""

from datetime import datetime

from parkmind.core.contracts import Event
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase


class PostgresEventRepository(PostgresRepositoryBase):
    def record(self, thread_id: str, event: Event) -> bool:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO events (thread_id, event_id, type, source, occurred_at, payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (thread_id, event_id) DO NOTHING
                RETURNING event_id
                """,
                (
                    thread_id,
                    event.event_id,
                    event.type.value,
                    event.source.value,
                    event.timestamp,
                    to_jsonb(event),
                ),
            )
            return cur.fetchone() is not None

    def list_for_thread(
        self, thread_id: str, *, since: datetime | None = None
    ) -> list[Event]:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT payload FROM events
                WHERE thread_id = %s AND (%s::timestamptz IS NULL OR occurred_at >= %s)
                ORDER BY occurred_at, event_id
                """,
                (thread_id, since, since),
            )
            rows = cur.fetchall()
        return [from_payload(Event, r["payload"], what="event") for r in rows]
