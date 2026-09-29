"""SnapshotRepository -- persisted ``LiveContext`` snapshots (section 41 [C21]).

Each snapshot keeps the normalized ``LiveContext`` *beside* the raw provider
payload, so an ID-mapping bug can be re-normalized instead of re-collected.
The raw payload is opaque to the core: stored and returned verbatim, never
parsed here. ``normalizer_version`` records which normalizer produced the
``LiveContext``; the collector, "latest valid snapshot" and re-normalization
(P0-11) are layered on top of this port in ``services.use_cases``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from parkmind.core.contracts import DataSource, LiveContext

RawPayload = Mapping[str, Any]


@dataclass(frozen=True)
class SnapshotMeta:
    """A snapshot's bookkeeping, readable even when its ``live_context`` no longer
    validates against the current contract."""

    snapshot_id: str
    retrieved_at: datetime
    data_sources: list[DataSource]
    normalizer_version: int


class SnapshotRepository(Protocol):
    def save(
        self,
        snapshot: LiveContext,
        raw_payload: RawPayload,
        data_sources: Sequence[DataSource],
        *,
        normalizer_version: int,
    ) -> bool:
        """Store the snapshot with its raw payload, sources and normalizer version.

        ``snapshot_id`` is the idempotency key: returns ``False`` and leaves the
        stored row untouched when it already exists.
        """
        ...

    def replace_normalized(self, snapshot: LiveContext, *, normalizer_version: int) -> bool:
        """Rewrite one snapshot's ``live_context`` and ``normalizer_version`` only.

        ``raw_payload``, ``data_sources`` and ``retrieved_at`` are never touched.
        Returns ``False`` if no snapshot has this id. Raises ``ValueError`` if
        ``snapshot.retrieved_at`` differs from the stored one: re-normalizing
        must not move a snapshot in time.
        """
        ...

    def get(self, snapshot_id: str) -> LiveContext | None: ...

    def get_meta(self, snapshot_id: str) -> SnapshotMeta | None: ...

    def get_raw_payload(self, snapshot_id: str) -> RawPayload | None: ...

    def get_data_sources(self, snapshot_id: str) -> list[DataSource] | None: ...

    def get_latest(self) -> LiveContext | None:
        """The snapshot with the greatest ``retrieved_at``, if any."""
        ...

    def recent_ids(self, limit: int) -> list[str]:
        """Up to ``limit`` snapshot ids, newest ``retrieved_at`` first."""
        ...

    def ids_below_version(self, normalizer_version: int) -> list[str]:
        """Ids of snapshots built by an older normalizer, oldest first."""
        ...
