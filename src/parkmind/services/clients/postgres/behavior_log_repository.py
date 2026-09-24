"""PostgreSQL adapter for ``BehaviorLogRepository`` (append-only, idempotent)."""

from parkmind.core.contracts import BehaviorEntry, BehaviorLog
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase


class PostgresBehaviorLogRepository(PostgresRepositoryBase):
    def append(self, guest_id: str, entry: BehaviorEntry) -> bool:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO behavior_entries
                    (guest_id, entry_id, event_type, occurred_at, payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (guest_id, entry_id) DO NOTHING
                RETURNING entry_id
                """,
                (
                    guest_id,
                    entry.entry_id,
                    entry.event_type.value,
                    entry.timestamp,
                    to_jsonb(entry),
                ),
            )
            return cur.fetchone() is not None

    def get(self, guest_id: str) -> BehaviorLog:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT payload FROM behavior_entries
                WHERE guest_id = %s ORDER BY occurred_at, entry_id
                """,
                (guest_id,),
            )
            rows = cur.fetchall()
        return BehaviorLog(
            guest_id=guest_id,
            entries=[from_payload(BehaviorEntry, r["payload"], what="behavior entry") for r in rows],
        )
