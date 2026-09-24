"""SnapshotRepository -- persisted ``LiveContext`` snapshots (section 41 [C21]).

Each snapshot keeps the normalized ``LiveContext`` *beside* the raw provider
payload, so an ID-mapping bug can be re-normalized instead of re-collected.
The raw payload is opaque to the core: stored and returned verbatim, never
parsed here. Collector idempotency, snapshot age/validity and the
re-normalization command are P0-11, layered on top of this port.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from parkmind.core.contracts import DataSource, LiveContext

RawPayload = Mapping[str, Any]


class SnapshotRepository(Protocol):
    def save(
        self,
        snapshot: LiveContext,
        raw_payload: RawPayload,
        data_sources: Sequence[DataSource],
    ) -> bool:
        """Store the snapshot with its raw payload and sources.

        ``snapshot_id`` is the idempotency key: returns ``False`` and leaves the
        stored row untouched when it already exists.
        """
        ...

    def get(self, snapshot_id: str) -> LiveContext | None: ...

    def get_raw_payload(self, snapshot_id: str) -> RawPayload | None: ...

    def get_data_sources(self, snapshot_id: str) -> list[DataSource] | None: ...

    def get_latest(self) -> LiveContext | None:
        """The snapshot with the greatest ``retrieved_at``, if any."""
        ...
