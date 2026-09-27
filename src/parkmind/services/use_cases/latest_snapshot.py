"""The most recent valid snapshot and its age (backlog P0-11).

This is the "recent cached snapshot" step of section 43's chain -- live data ->
recent cached snapshot -> historical profile -- and what rule 11
(``DATA_FRESHNESS``) needs to know: how old the data is **at ``now``**. Age is
always measured from the ``now`` the caller passes, never from anything inside
a plan.

"Valid" means the stored ``LiveContext`` still validates against the current
contract. A newer row that doesn't (``StoredDataError``) is skipped and named in
``skipped`` instead of making the lookup fail; re-normalization can repair it.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from parkmind.core.contracts import LiveContext
from parkmind.services.ports import SnapshotRepository, StoredDataError

DEFAULT_MAX_AGE = timedelta(minutes=30)
DEFAULT_LOOKBACK = 20


@dataclass(frozen=True)
class LatestSnapshot:
    live_context: LiveContext
    age: timedelta
    fresh: bool
    """``True`` when ``0 <= age <= max_age``. A snapshot dated after ``now`` is
    not trusted as fresh."""
    skipped: list[str] = field(default_factory=list)
    """Newer snapshot ids passed over because they no longer validate."""


def latest_valid_snapshot(
    snapshots: SnapshotRepository,
    *,
    now: datetime,
    max_age: timedelta = DEFAULT_MAX_AGE,
    lookback: int = DEFAULT_LOOKBACK,
) -> LatestSnapshot | None:
    """The newest snapshot whose ``LiveContext`` validates, with its age at ``now``.

    Looks at the ``lookback`` most recent snapshots; ``None`` if none of them is
    valid (or there are none).
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    skipped: list[str] = []
    for snapshot_id in snapshots.recent_ids(lookback):
        try:
            live_context = snapshots.get(snapshot_id)
        except StoredDataError:
            skipped.append(snapshot_id)
            continue
        if live_context is None:  # deleted between the two calls
            continue
        age = now - live_context.retrieved_at
        return LatestSnapshot(
            live_context=live_context,
            age=age,
            fresh=timedelta(0) <= age <= max_age,
            skipped=skipped,
        )
    return None
