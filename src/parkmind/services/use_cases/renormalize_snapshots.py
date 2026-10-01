"""Re-normalization: rebuild stored snapshots from their raw payloads (P0-11, C21).

"Snapshots store the raw provider payload beside the normalized rows, so an
ID-mapping bug can be re-normalized instead of re-collected" (Architecture 41).
For each snapshot, the stored ``raw_payload`` goes through the *current*
normalizers and ``IdResolver`` again -- the same ``normalize_raw_snapshot`` the
collector uses -- and only ``live_context`` + ``normalizer_version`` are
rewritten. ``raw_payload``, ``data_sources`` and ``retrieved_at`` never change,
and ``seen_at`` for id mappings is the snapshot's own ``retrieved_at``.

One snapshot that can't be rebuilt is reported as ``failed`` with its reason;
it never stops the rest.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from parkmind.services.clients.normalization import NORMALIZER_VERSION
from parkmind.services.clients.themeparks_errors import ThemeParksClientError
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
    MAGIC_KINGDOM_SCHEDULED_SHOWS,
    AttractionMetadata,
)
from parkmind.services.ports import (
    IdMappingRepository,
    SnapshotRepository,
    StoredDataError,
)
from parkmind.services.use_cases.id_resolution import IdResolver
from parkmind.services.use_cases.snapshot_normalization import normalize_raw_snapshot

MAX_STORED_VERSION = 2**31 - 1  # the column is a Postgres INTEGER

Status = Literal["rebuilt", "unchanged", "failed"]


@dataclass(frozen=True)
class RenormalizeOutcome:
    snapshot_id: str
    status: Status
    reason: str | None = None


@dataclass(frozen=True)
class RenormalizeReport:
    outcomes: list[RenormalizeOutcome] = field(default_factory=list)

    def count(self, status: Status) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def failed(self) -> list[RenormalizeOutcome]:
        return [outcome for outcome in self.outcomes if outcome.status == "failed"]


def stale_snapshot_ids(
    snapshots: SnapshotRepository, *, target_version: int = NORMALIZER_VERSION
) -> list[str]:
    """Snapshots built by a normalizer older than ``target_version``, oldest first."""
    return snapshots.ids_below_version(target_version)


def all_snapshot_ids(snapshots: SnapshotRepository) -> list[str]:
    return snapshots.ids_below_version(MAX_STORED_VERSION + 1)


def renormalize_snapshots(
    snapshots: SnapshotRepository,
    id_mappings: IdMappingRepository,
    snapshot_ids: Iterable[str],
    *,
    target_version: int = NORMALIZER_VERSION,
    curated: Mapping[str, AttractionMetadata] = MAGIC_KINGDOM_ATTRACTION_METADATA,
    scheduled_shows: Collection[str] = MAGIC_KINGDOM_SCHEDULED_SHOWS,
) -> RenormalizeReport:
    resolver = IdResolver(id_mappings)
    outcomes = [
        _renormalize_one(
            snapshots, resolver, snapshot_id, target_version, curated, scheduled_shows
        )
        for snapshot_id in snapshot_ids
    ]
    return RenormalizeReport(outcomes=outcomes)


def _renormalize_one(
    snapshots: SnapshotRepository,
    resolver: IdResolver,
    snapshot_id: str,
    target_version: int,
    curated: Mapping[str, AttractionMetadata],
    scheduled_shows: Collection[str],
) -> RenormalizeOutcome:
    meta = snapshots.get_meta(snapshot_id)
    raw = snapshots.get_raw_payload(snapshot_id)
    if meta is None or raw is None:
        return RenormalizeOutcome(snapshot_id, "failed", "no such snapshot")
    try:
        rebuilt = normalize_raw_snapshot(
            raw,
            snapshot_id=snapshot_id,
            retrieved_at=meta.retrieved_at,
            resolver=resolver,
            curated=curated,
            scheduled_shows=scheduled_shows,
        ).live_context
    except (ValueError, ThemeParksClientError) as exc:
        # ValueError covers RawSnapshotError and pydantic's ValidationError.
        return RenormalizeOutcome(snapshot_id, "failed", f"{type(exc).__name__}: {exc}")

    try:
        current = snapshots.get(snapshot_id)
    except StoredDataError:
        current = None  # the stored LiveContext no longer validates: rebuild it
    if current == rebuilt and meta.normalizer_version == target_version:
        return RenormalizeOutcome(snapshot_id, "unchanged")

    snapshots.replace_normalized(rebuilt, normalizer_version=target_version)
    return RenormalizeOutcome(snapshot_id, "rebuilt")
