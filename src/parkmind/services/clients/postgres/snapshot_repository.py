"""PostgreSQL adapter for ``SnapshotRepository`` (section 41 [C21]).

Stores the normalized ``LiveContext`` beside the raw provider payload. Nothing
here interprets the raw payload.
"""

from collections.abc import Sequence

from parkmind.core.contracts import DataSource, LiveContext
from parkmind.services.clients.postgres.codec import (
    from_payload,
    raw_to_jsonb,
    to_jsonb,
)
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase
from parkmind.services.ports.errors import StoredDataError
from parkmind.services.ports.snapshot_repository import RawPayload


class PostgresSnapshotRepository(PostgresRepositoryBase):
    def save(
        self,
        snapshot: LiveContext,
        raw_payload: RawPayload,
        data_sources: Sequence[DataSource],
    ) -> bool:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO snapshots
                    (snapshot_id, retrieved_at, data_sources, live_context, raw_payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_id) DO NOTHING
                RETURNING snapshot_id
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.retrieved_at,
                    [source.value for source in data_sources],
                    to_jsonb(snapshot),
                    raw_to_jsonb(raw_payload),
                ),
            )
            return cur.fetchone() is not None

    def get(self, snapshot_id: str) -> LiveContext | None:
        with self._tx() as cur:
            cur.execute(
                "SELECT live_context FROM snapshots WHERE snapshot_id = %s",
                (snapshot_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return from_payload(LiveContext, row["live_context"], what="snapshot")

    def get_raw_payload(self, snapshot_id: str) -> RawPayload | None:
        with self._tx() as cur:
            cur.execute(
                "SELECT raw_payload FROM snapshots WHERE snapshot_id = %s",
                (snapshot_id,),
            )
            row = cur.fetchone()
        return None if row is None else dict(row["raw_payload"])

    def get_data_sources(self, snapshot_id: str) -> list[DataSource] | None:
        with self._tx() as cur:
            cur.execute(
                "SELECT data_sources FROM snapshots WHERE snapshot_id = %s",
                (snapshot_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        try:
            return [DataSource(value) for value in row["data_sources"]]
        except ValueError:
            raise StoredDataError("stored snapshot names an unknown data source") from None

    def get_latest(self) -> LiveContext | None:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT live_context FROM snapshots
                ORDER BY retrieved_at DESC, snapshot_id DESC LIMIT 1
                """
            )
            row = cur.fetchone()
        if row is None:
            return None
        return from_payload(LiveContext, row["live_context"], what="snapshot")
