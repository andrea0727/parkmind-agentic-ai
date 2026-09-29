"""In-memory fakes of repository ports, for unit tests that must not touch a DB.

They keep the Postgres adapters' documented semantics (idempotent save,
conflicting id re-map raises, re-normalization keeps retrieved_at), and
tests/integration/postgres/ runs the same use cases against the real adapters.
Only ports are faked -- never the core.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from parkmind.core.contracts import DataSource, LiveContext
from parkmind.services.ports import IdMappingConflictError, SnapshotMeta


class InMemoryIdMappingRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], str] = {}

    def record(
        self,
        provider: str,
        provider_id: str,
        entity_kind: str,
        internal_id: str,
        *,
        seen_at: datetime,
    ) -> None:
        key = (provider, provider_id, str(entity_kind))
        if self.rows.setdefault(key, internal_id) != internal_id:
            raise IdMappingConflictError("provider id already mapped elsewhere")

    def resolve(self, provider: str, provider_id: str, entity_kind: str) -> str | None:
        return self.rows.get((provider, provider_id, str(entity_kind)))

    def provider_ids_for(self, internal_id: str) -> list[tuple[str, str, str]]:
        return sorted(k for k, v in self.rows.items() if v == internal_id)


class InMemorySnapshotRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def save(
        self,
        snapshot: LiveContext,
        raw_payload: Any,
        data_sources: Sequence[DataSource],
        *,
        normalizer_version: int,
    ) -> bool:
        if snapshot.snapshot_id in self.rows:
            return False
        self.rows[snapshot.snapshot_id] = {
            "live_context": snapshot,
            "raw": dict(raw_payload),
            "sources": list(data_sources),
            "version": normalizer_version,
        }
        return True

    def replace_normalized(self, snapshot: LiveContext, *, normalizer_version: int) -> bool:
        row = self.rows.get(snapshot.snapshot_id)
        if row is None:
            return False
        if row["live_context"].retrieved_at != snapshot.retrieved_at:
            raise ValueError("re-normalizing must keep the stored retrieved_at")
        row["live_context"], row["version"] = snapshot, normalizer_version
        return True

    def get(self, snapshot_id: str) -> LiveContext | None:
        row = self.rows.get(snapshot_id)
        return None if row is None else row["live_context"]

    def get_meta(self, snapshot_id: str) -> SnapshotMeta | None:
        row = self.rows.get(snapshot_id)
        if row is None:
            return None
        return SnapshotMeta(
            snapshot_id=snapshot_id,
            retrieved_at=row["live_context"].retrieved_at,
            data_sources=row["sources"],
            normalizer_version=row["version"],
        )

    def get_raw_payload(self, snapshot_id: str) -> Any:
        row = self.rows.get(snapshot_id)
        return None if row is None else row["raw"]

    def get_data_sources(self, snapshot_id: str) -> list[DataSource] | None:
        row = self.rows.get(snapshot_id)
        return None if row is None else row["sources"]

    def get_latest(self) -> LiveContext | None:
        ids = self.recent_ids(1)
        return self.get(ids[0]) if ids else None

    def recent_ids(self, limit: int) -> list[str]:
        ordered = sorted(
            self.rows,
            key=lambda sid: (self.rows[sid]["live_context"].retrieved_at, sid),
            reverse=True,
        )
        return ordered[: max(limit, 0)]

    def ids_below_version(self, normalizer_version: int) -> list[str]:
        return sorted(
            (sid for sid, row in self.rows.items() if row["version"] < normalizer_version),
            key=lambda sid: (self.rows[sid]["live_context"].retrieved_at, sid),
        )
