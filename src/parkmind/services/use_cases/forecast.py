"""Wiring the ForecastService from stored snapshots (P0-18).

``services/planning/forecast_service.py`` holds the pure service and strategies;
this module feeds them data from the ports, which the deterministic core may not
import:

- ``build_wait_profile`` -- the historical profile (section 18: "built from our
  own Postgres snapshots"): median standby wait per (attraction, park-local hour).

Left open on purpose (not decided in code): whether the profile should also key
on weekday or season, and how many days of history it should span.
"""

from collections import defaultdict
from datetime import datetime
from statistics import median

from parkmind.core.contracts import PARK_TZ, AttractionStatus
from parkmind.services.planning.forecast_service import WaitProfile
from parkmind.services.ports import SnapshotRepository, StoredDataError

DEFAULT_PROFILE_LOOKBACK = 2000
"""Snapshots read for the profile: about two weeks at one snapshot per 10 minutes."""
DEFAULT_MIN_SAMPLES = 3
"""Readings an (attraction, hour) cell needs before its median is trusted."""


def build_wait_profile(
    snapshots: SnapshotRepository,
    *,
    now: datetime,
    lookback: int = DEFAULT_PROFILE_LOOKBACK,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> WaitProfile:
    """Median OPERATING standby wait per ``(attraction_id, park-local hour)``.

    Reads the ``lookback`` most recent snapshots. A snapshot dated after ``now``
    is ignored (the profile never sees the future of the moment it is built
    for), as is a row that no longer validates (``StoredDataError``; re-
    normalization repairs those). A posted wait of a DOWN or CLOSED ride is not
    a queue anyone stood in, so only OPERATING waits count. A cell with fewer
    than ``min_samples`` readings is left out: no profile for it.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1")
    samples: dict[tuple[str, int], list[float]] = defaultdict(list)
    for snapshot_id in snapshots.recent_ids(lookback):
        try:
            context = snapshots.get(snapshot_id)
        except StoredDataError:
            continue
        if context is None or context.retrieved_at > now:
            continue
        hour = context.retrieved_at.astimezone(PARK_TZ).hour
        for attraction_id, estimate in context.waits.items():
            if estimate.status == AttractionStatus.OPERATING:
                samples[(attraction_id, hour)].append(estimate.wait_minutes)
    kept = {cell: waits for cell, waits in sorted(samples.items()) if len(waits) >= min_samples}
    return WaitProfile(
        medians={cell: float(median(waits)) for cell, waits in kept.items()},
        built_at=now,
        sample_counts={cell: len(waits) for cell, waits in kept.items()},
    )
