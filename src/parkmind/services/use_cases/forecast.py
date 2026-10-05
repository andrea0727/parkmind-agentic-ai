"""Wiring the ForecastService from stored snapshots (P0-18).

``services/planning/forecast_service.py`` holds the pure service and strategies;
this module feeds them data from the ports, which the deterministic core may not
import:

- ``build_wait_profile`` -- the historical profile (section 18: "built from our
  own Postgres snapshots"): median standby wait per (attraction, park-local hour).
- ``build_forecast_service`` -- section 43's chain for one planning run at
  ``now``: API forecast (the ``forecast`` field of the latest valid snapshot's raw
  ``/live`` payload, so forecast and ``LiveContext`` share one ``snapshot_id``)
  -> historical profile -> that snapshot's current wait while it is fresh.

Left open on purpose (not decided in code): whether the profile should also key
on weekday or season, and how many days of history it should span.
"""

import logging
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from parkmind.core.contracts import PARK_TZ, AttractionStatus, DataSource, LiveContext
from parkmind.services.clients.themeparks_errors import ThemeParksSchemaError
from parkmind.services.clients.themeparks_normalize import (
    check_timezone,
    entity_kind,
    index_entities,
    parse_forecast,
)
from parkmind.services.planning.forecast_service import (
    ApiForecastStrategy,
    CachedSnapshotStrategy,
    ForecastPoints,
    ForecastService,
    HistoricalProfileStrategy,
    WaitProfile,
)
from parkmind.services.ports import (
    ForecastStrategy,
    IdMappingRepository,
    SnapshotRepository,
    StoredDataError,
)
from parkmind.services.use_cases.latest_snapshot import (
    DEFAULT_MAX_AGE,
    latest_valid_snapshot,
)

logger = logging.getLogger(__name__)

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


def build_forecast_service(
    snapshots: SnapshotRepository,
    id_mappings: IdMappingRepository,
    *,
    now: datetime,
    max_age: timedelta = DEFAULT_MAX_AGE,
    lookback: int = DEFAULT_PROFILE_LOOKBACK,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> ForecastService:
    """The ``ForecastService`` for one planning run at ``now``.

    Strategies, in order: API forecast -> historical profile -> cached snapshot.
    The two snapshot-based strategies use the latest valid snapshot and are
    built only when it is fresh at ``now`` (``max_age``, rule 11's window): a
    stale snapshot can't turn fresh for a later ``now``, so its raw payload is
    not even read. With no fresh snapshot only the profile is left. Each
    strategy still re-checks freshness per call, for a service used after it
    was built. A raw payload whose forecast can't be read is an API failure:
    the API strategy is left out and logged, and the chain falls back
    (section 43).
    """
    latest = latest_valid_snapshot(snapshots, now=now, max_age=max_age)
    fresh = latest if latest is not None and latest.fresh else None
    strategies: list[ForecastStrategy] = []
    if fresh is not None:
        api = _api_strategy(snapshots, id_mappings, fresh.live_context, max_age=max_age)
        if api is not None:
            strategies.append(api)
    strategies.append(
        HistoricalProfileStrategy(
            build_wait_profile(snapshots, now=now, lookback=lookback, min_samples=min_samples)
        )
    )
    if fresh is not None:
        strategies.append(CachedSnapshotStrategy(fresh.live_context, max_age=max_age))
    return ForecastService(strategies)


def _api_strategy(
    snapshots: SnapshotRepository,
    id_mappings: IdMappingRepository,
    context: LiveContext,
    *,
    max_age: timedelta,
) -> ApiForecastStrategy | None:
    """The provider forecast from ``context``'s own raw payload, or ``None`` if unreadable."""
    raw = snapshots.get_raw_payload(context.snapshot_id)
    try:
        points = _forecast_points(raw, id_mappings)
    except (ThemeParksSchemaError, KeyError, TypeError, AttributeError) as exc:
        logger.warning(
            "API forecast unavailable for snapshot %s; falling back: %s", context.snapshot_id, exc
        )
        return None
    return ApiForecastStrategy(
        points,
        snapshot_id=context.snapshot_id,
        retrieved_at=context.retrieved_at,
        max_age=max_age,
    )


def _forecast_points(
    raw: Mapping[str, Any] | None, id_mappings: IdMappingRepository
) -> dict[str, ForecastPoints]:
    """Forecast series by internal id from a collector raw payload.

    Provider ids become internal ids through a read-only lookup of the mappings
    the collector recorded; an id without a mapping gets no API forecast (it
    falls back) rather than a guessed one. As in normalization, a duplicated
    provider id is excluded, and so is an internal id that two provider ids
    resolve to (``DUPLICATE_INTERNAL_ID``): neither series is picked, and the
    id falls back.
    """
    if raw is None:
        raise KeyError("no raw payload stored for the snapshot")
    live = raw["themeparks"]["live"]
    check_timezone(live)
    index, _issues = index_entities(live.get("liveData", []))
    by_internal: dict[str, list[tuple[str, ForecastPoints]]] = defaultdict(list)
    for provider_id, entity in sorted(index.items()):
        series = parse_forecast(entity)
        kind = entity_kind(entity)
        if not series or kind is None:
            continue
        internal_id = id_mappings.resolve(DataSource.THEMEPARKS_WIKI.value, provider_id, kind)
        if internal_id is not None:
            by_internal[internal_id].append((provider_id, series))
    points: dict[str, ForecastPoints] = {}
    for internal_id, sources in by_internal.items():
        if len(sources) > 1:
            logger.warning(
                "no API forecast for %s: provider ids %s all resolve to it",
                internal_id,
                ", ".join(provider_id for provider_id, _ in sources),
            )
            continue
        points[internal_id] = sources[0][1]
    return points
