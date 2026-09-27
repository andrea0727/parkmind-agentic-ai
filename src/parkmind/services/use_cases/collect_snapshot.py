"""Snapshot collector (backlog P0-11, Architecture 41 [C21]).

Fetches the park's live data (ThemeParks ``/live`` + ``/schedule``) and the
hourly weather (Open-Meteo), stores the raw payloads verbatim beside the
normalized ``LiveContext``, and records which normalizer version built it.

**Idempotent by collection window.** The snapshot id is derived from the park
and ``now`` floored to the collection interval (default 5 minutes, park time).
A retry or a schedule that fires twice in the same window finds the snapshot
already stored and writes nothing -- no provider call, no duplicate row. The
next window is a new snapshot: that is the history.

**Degrade, don't fail (section 43).** Without ThemeParks live data there is no
snapshot (the call raises). A missing schedule or weather payload is recorded
in ``degraded`` and as a coverage gap, and the snapshot is still stored.

``now`` is always a parameter; nothing here reads the clock.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from parkmind.core.contracts import DataSource, LiveContext
from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.normalization import NORMALIZER_VERSION, MappingIssue
from parkmind.services.clients.open_meteo_client import OpenMeteoClient
from parkmind.services.clients.open_meteo_errors import OpenMeteoClientError
from parkmind.services.clients.themeparks_client import ThemeParksClient
from parkmind.services.clients.themeparks_errors import ThemeParksClientError
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
    AttractionMetadata,
)
from parkmind.services.ports import IdMappingRepository, SnapshotRepository
from parkmind.services.use_cases.id_resolution import IdResolver
from parkmind.services.use_cases.snapshot_normalization import (
    RAW_SCHEMA,
    normalize_raw_snapshot,
)

DEFAULT_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class CollectResult:
    snapshot_id: str
    created: bool
    """``False`` when this collection window was already stored (nothing was written)."""
    live_context: LiveContext | None = None
    data_sources: list[DataSource] = field(default_factory=list)
    issues: list[MappingIssue] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)


def snapshot_id_for(park_id: str, now: datetime, interval: timedelta = DEFAULT_INTERVAL) -> str:
    """The idempotency key: park + ``now`` floored to the interval, in park time."""
    _require_aware(now)
    if interval <= timedelta(0) or interval > timedelta(days=1):
        raise ValueError("interval must be positive and at most one day")
    local = now.astimezone(PARK_TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    bucket = midnight + ((local - midnight) // interval) * interval
    return f"snap_{park_id}_{bucket:%Y%m%dT%H%M}"


class SnapshotCollector:
    def __init__(
        self,
        parks: ThemeParksClient,
        weather: OpenMeteoClient,
        snapshots: SnapshotRepository,
        id_mappings: IdMappingRepository,
        *,
        interval: timedelta = DEFAULT_INTERVAL,
        latitude: float = OpenMeteoClient.DEFAULT_LATITUDE,
        longitude: float = OpenMeteoClient.DEFAULT_LONGITUDE,
        curated: Mapping[str, AttractionMetadata] = MAGIC_KINGDOM_ATTRACTION_METADATA,
    ) -> None:
        self._parks = parks
        self._weather = weather
        self._snapshots = snapshots
        self._resolver = IdResolver(id_mappings)
        self._interval = interval
        self._latitude = latitude
        self._longitude = longitude
        self._curated = curated

    def collect(self, *, now: datetime) -> CollectResult:
        """Take one snapshot for the collection window containing ``now``.

        Raises ``ThemeParksClientError`` when the park's live data can't be
        fetched or read: without it there is nothing worth storing.
        """
        snapshot_id = snapshot_id_for(self._parks.park_id, now, self._interval)
        if self._snapshots.get_meta(snapshot_id) is not None:
            return CollectResult(snapshot_id=snapshot_id, created=False)

        raw = self._fetch_raw(now)
        normalized = normalize_raw_snapshot(
            raw,
            snapshot_id=snapshot_id,
            retrieved_at=now,
            resolver=self._resolver,
            curated=self._curated,
        )
        created = self._snapshots.save(
            normalized.live_context,
            raw,
            normalized.data_sources,
            normalizer_version=NORMALIZER_VERSION,
        )
        return CollectResult(
            snapshot_id=snapshot_id,
            created=created,
            live_context=normalized.live_context,
            data_sources=normalized.data_sources,
            issues=normalized.issues,
            degraded=list(raw["degraded"]),
        )

    def _fetch_raw(self, now: datetime) -> dict[str, Any]:
        service_date = now.astimezone(PARK_TZ).date()
        degraded: list[str] = []

        live = self._parks.fetch_live_payload()  # required: raises if unavailable

        schedule: dict[str, Any] | None
        try:
            schedule = self._parks.fetch_schedule_payload()
        except ThemeParksClientError as exc:
            schedule = None
            degraded.append(f"themeparks schedule: {type(exc).__name__}: {exc}")

        hourly: dict[str, Any] | None
        try:
            hourly = self._weather.fetch_hourly_payload(
                latitude=self._latitude,
                longitude=self._longitude,
                start_date=service_date,
                end_date=service_date,
            )
        except OpenMeteoClientError as exc:
            hourly = None
            degraded.append(f"open_meteo: {type(exc).__name__}: {exc}")

        return {
            "raw_schema": RAW_SCHEMA,
            "request": {
                "park_id": self._parks.park_id,
                "service_date": service_date.isoformat(),
                "latitude": self._latitude,
                "longitude": self._longitude,
            },
            "themeparks": {"live": live, "schedule": schedule},
            "open_meteo": None if hourly is None else {"hourly": hourly},
            "degraded": degraded,
        }


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
