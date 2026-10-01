"""Raw snapshot payload -> ``LiveContext`` (backlog P0-11).

The one place a stored raw payload becomes a normalized ``LiveContext``. The
collector uses it when a snapshot is taken, and the re-normalization command
uses it again later on the stored payload (Architecture 41, C21) -- so both
always produce exactly the same thing from the same raw data.

Raw payload layout (``raw_schema`` 1)::

    {"raw_schema": 1,
     "request": {"park_id", "service_date", "latitude", "longitude"},
     "themeparks": {"live": <verbatim /live>, "schedule": <verbatim /schedule> | null},
     "open_meteo": {"hourly": <verbatim /forecast>} | null,
     "degraded": ["open_meteo: ...", ...]}

Degrade, don't fail (section 43): the ThemeParks live payload is required; a
missing or unreadable schedule or weather payload leaves a coverage gap instead
of failing the snapshot.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from parkmind.core.contracts import (
    AttractionStatus,
    CoverageReport,
    DataSource,
    LiveContext,
    Park,
    WaitEstimate,
    WeatherHour,
)
from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.normalization import MappingIssue
from parkmind.services.clients.open_meteo_errors import OpenMeteoSchemaError
from parkmind.services.clients.open_meteo_normalize import parse_hourly_forecast
from parkmind.services.clients.themeparks_errors import ThemeParksClientError
from parkmind.services.clients.themeparks_normalize import parse_live, parse_schedule
from parkmind.services.clients.themeparks_reference_data import AttractionMetadata
from parkmind.services.use_cases.id_resolution import IdResolver

RAW_SCHEMA = 1
ACCESSIBILITY_GAP = "accessibility checks are per party: done by load_context (P0-30), not the collector"


class RawSnapshotError(ValueError):
    """The stored raw payload isn't a collector snapshot this code can read."""


@dataclass(frozen=True)
class NormalizedSnapshot:
    live_context: LiveContext
    data_sources: list[DataSource]
    issues: list[MappingIssue] = field(default_factory=list)


def normalize_raw_snapshot(
    raw: Mapping[str, Any],
    *,
    snapshot_id: str,
    retrieved_at: datetime,
    resolver: IdResolver,
    curated: Mapping[str, AttractionMetadata],
    scheduled_shows: Collection[str],
) -> NormalizedSnapshot:
    """Rebuild the ``LiveContext`` for one raw snapshot payload.

    Provider ids become internal ids through ``resolver`` (``seen_at`` is the
    snapshot's own ``retrieved_at``). ``curated`` is the curated catalog the
    coverage report is measured against; ``scheduled_shows`` are the curated ids
    that run on scheduled starts and so need showtimes while they operate.
    """
    if raw.get("raw_schema") != RAW_SCHEMA:
        raise RawSnapshotError(f"unsupported raw_schema {raw.get('raw_schema')!r}")
    try:
        request = raw["request"]
        themeparks = raw["themeparks"]
        live_payload = themeparks["live"]
        park_id = request["park_id"]
        service_date = date.fromisoformat(request["service_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RawSnapshotError(f"malformed raw snapshot payload: {exc}") from None

    gaps = [f"degraded at collection: {reason}" for reason in raw.get("degraded", [])]

    live = resolver.resolve_live(
        DataSource.THEMEPARKS_WIKI, parse_live(live_payload), seen_at=retrieved_at
    )
    entities = live.entities
    waits = {
        entity_id: WaitEstimate(
            attraction_id=entity_id, wait_minutes=entity.standby_wait_minutes, status=entity.status
        )
        for entity_id, entity in entities.items()
        if entity.standby_wait_minutes is not None
    }
    statuses = {entity_id: entity.status for entity_id, entity in entities.items()}
    showtimes = {
        entity_id: list(entity.showtimes)
        for entity_id, entity in entities.items()
        if entity.showtimes
    }

    # data_sources records what was *collected* (it is stored once and never
    # rewritten by re-normalization); a collected payload that can't be read
    # shows up as a coverage gap instead.
    data_sources = [DataSource.THEMEPARKS_WIKI]
    weather: list[WeatherHour] = []
    open_meteo = raw.get("open_meteo")
    if open_meteo is not None:
        data_sources.append(DataSource.OPEN_METEO)
        try:
            weather = parse_hourly_forecast(open_meteo.get("hourly"))
        except OpenMeteoSchemaError as exc:
            gaps.append(f"open_meteo payload rejected: {exc}")

    park = _park_window(themeparks.get("schedule"), service_date, park_id, gaps)
    coverage = _coverage(
        curated=curated,
        scheduled_shows=scheduled_shows,
        statuses=statuses,
        showtimes=showtimes,
        weather=weather,
        park=park,
        gaps=gaps,
    )
    return NormalizedSnapshot(
        live_context=LiveContext(
            snapshot_id=snapshot_id,
            retrieved_at=retrieved_at,
            waits=waits,
            statuses=statuses,
            showtimes=showtimes,
            weather=weather,
            coverage=coverage,
        ),
        data_sources=data_sources,
        issues=list(live.issues),
    )


def _park_window(
    schedule: Mapping[str, Any] | None, service_date: date, park_id: str, gaps: list[str]
) -> Park | None:
    if schedule is None:
        gaps.append("no park schedule collected: weather coverage can't be checked")
        return None
    try:
        # Only the operating window is read (weather coverage below); this Park is
        # never stored or returned, so park_name=park_id is a placeholder, not the
        # park's real name -- don't reuse it where the name matters.
        return parse_schedule(
            schedule, service_date, park_id=park_id, park_name=park_id, park_outdoor=True
        )
    except ThemeParksClientError as exc:
        gaps.append(f"no usable OPERATING schedule for {service_date.isoformat()}: {exc}")
        return None


def _coverage(
    *,
    curated: Mapping[str, AttractionMetadata],
    scheduled_shows: Collection[str],
    statuses: Mapping[str, Any],
    showtimes: Mapping[str, Any],
    weather: list[WeatherHour],
    park: Park | None,
    gaps: list[str],
) -> CoverageReport:
    # Which entities run on scheduled starts comes from curated data, never from
    # this payload's ``kind``: a curated show that vanished from /live must still
    # count as missing (#64 review). Only *relevant* shows need showtimes
    # (Architecture 8.1, "every relevant show has showtimes"): one that is
    # OPERATING or whose status is unknown. A CLOSED, DOWN or REFURBISHMENT show
    # can't be planned today -- rule 1 already forbids it -- so on a party night,
    # when the regular fireworks don't run, its missing showtimes are no gap.
    relevant_shows = [
        i for i in scheduled_shows if statuses.get(i) in (None, AttractionStatus.OPERATING)
    ]

    missing_status = sorted(i for i in curated if i not in statuses)
    if missing_status:
        gaps.append(f"{len(missing_status)} curated attraction(s) without a status")

    missing_showtimes = sorted(i for i in relevant_shows if i not in showtimes)
    if not scheduled_shows:
        gaps.append("no scheduled shows are curated: show coverage is vacuous")
    elif missing_showtimes:
        gaps.append(f"{len(missing_showtimes)} operating show(s) without showtimes")

    weather_covered = False
    if not weather:
        gaps.append("no weather for this snapshot")
    elif park is not None:
        weather_covered = _weather_spans(weather, park.opening_time, park.closing_time)
        if not weather_covered:
            gaps.append("weather doesn't cover every hour the park is open")

    gaps.append(ACCESSIBILITY_GAP)
    return CoverageReport(
        required_attractions_covered=not missing_status,
        required_shows_covered=not missing_showtimes,
        weather_covered=weather_covered,
        accessibility_checks_complete=False,
        coverage_gaps=gaps,
    )


def _weather_spans(weather: list[WeatherHour], opening: datetime, closing: datetime) -> bool:
    hours = {h.timestamp.astimezone(PARK_TZ).replace(minute=0, second=0, microsecond=0) for h in weather}
    hour = opening.astimezone(PARK_TZ).replace(minute=0, second=0, microsecond=0)
    while hour < closing:
        if hour not in hours:
            return False
        hour += timedelta(hours=1)
    return True
