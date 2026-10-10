"""PostgreSQL adapter for ``SnapshotRepository`` (section 41 [C21]).

Stores the normalized ``LiveContext`` beside the raw provider payload and the
version of the normalizer that produced it. Nothing here interprets the raw
payload.
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
from parkmind.services.ports.snapshot_repository import RawPayload, SnapshotMeta


def _sources(values: Sequence[str]) -> list[DataSource]:
    try:
        return [DataSource(value) for value in values]
    except ValueError:
        raise StoredDataError("stored snapshot names an unknown data source") from None


def _require_version(normalizer_version: int) -> None:
    if normalizer_version < 1:
        raise ValueError("normalizer_version must be >= 1")


class PostgresSnapshotRepository(PostgresRepositoryBase):
    def save(
        self,
        snapshot: LiveContext,
        raw_payload: RawPayload,
        data_sources: Sequence[DataSource],
        *,
        normalizer_version: int,
    ) -> bool:
        _require_version(normalizer_version)
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO snapshots
                    (snapshot_id, retrieved_at, data_sources, live_context, raw_payload,
                     normalizer_version)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_id) DO NOTHING
                RETURNING snapshot_id
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.retrieved_at,
                    [source.value for source in data_sources],
                    to_jsonb(snapshot),
                    raw_to_jsonb(raw_payload),
                    normalizer_version,
                ),
            )
            return cur.fetchone() is not None

    def replace_normalized(
        self, snapshot: LiveContext, *, normalizer_version: int
    ) -> bool:
        _require_version(normalizer_version)
        with self._tx() as cur:
            cur.execute(
                "SELECT retrieved_at FROM snapshots WHERE snapshot_id = %s FOR UPDATE",
                (snapshot.snapshot_id,),
            )
            row = cur.fetchone()
            if row is None:
                return False
            if row["retrieved_at"] != snapshot.retrieved_at:
                raise ValueError(
                    "re-normalizing must keep the stored retrieved_at "
                    f"({row['retrieved_at'].isoformat()})"
                )
            cur.execute(
                """
                UPDATE snapshots
                SET live_context = %s, normalizer_version = %s
                WHERE snapshot_id = %s
                """,
                (to_jsonb(snapshot), normalizer_version, snapshot.snapshot_id),
            )
            return True

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

    def get_meta(self, snapshot_id: str) -> SnapshotMeta | None:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT snapshot_id, retrieved_at, data_sources, normalizer_version
                FROM snapshots WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return SnapshotMeta(
            snapshot_id=row["snapshot_id"],
            retrieved_at=row["retrieved_at"],
            data_sources=_sources(row["data_sources"]),
            normalizer_version=row["normalizer_version"],
        )

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
        return None if row is None else _sources(row["data_sources"])

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

    def recent_ids(self, limit: int) -> list[str]:
        if limit < 1:
            return []
        with self._tx() as cur:
            cur.execute(
                """
                SELECT snapshot_id FROM snapshots
                ORDER BY retrieved_at DESC, snapshot_id DESC LIMIT %s
                """,
                (limit,),
            )
            return [row["snapshot_id"] for row in cur.fetchall()]

    def ids_below_version(self, normalizer_version: int) -> list[str]:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT snapshot_id FROM snapshots WHERE normalizer_version < %s
                ORDER BY retrieved_at, snapshot_id
                """,
                (normalizer_version,),
            )
            return [row["snapshot_id"] for row in cur.fetchall()]
