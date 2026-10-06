"""ThemeParks.wiki raw payload -> ParkMind contracts, with no HTTP (backlog P0-10).

Pure functions over the provider's JSON, used by ``ThemeParksClient`` after it
fetches a payload, and usable on a stored raw payload so a snapshot can be
re-normalized instead of re-collected (Architecture section 41, C21; P0-11).

Canonical values produced here:
- **Timezone:** a payload's top-level ``timezone`` must be the park's
  (``PARK_TZ``); anything else is contract drift and raises.
- **Timestamps:** every provider time becomes an aware ``PARK_TZ`` datetime. A
  timestamp without an offset raises -- it is never read as the machine's
  local time.
- **Status:** the closed ``AttractionStatus`` enum; an unknown value raises.
- **Entity kind:** the closed ``EntityKind`` vocabulary.

Identity problems are excluded and reported as ``MappingIssue``s (see
``normalization``); ids are provider ids here and become internal ids only in
``services.use_cases.id_resolution``.
"""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from datetime import date, datetime
from typing import Any

from parkmind.core.contracts import Attraction, AttractionStatus, DataSource, Park
from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.ports.id_mapping_repository import EntityKind

from .normalization import (
    IssueKind,
    LiveEntity,
    MappingIssue,
    NormalizedCatalog,
    NormalizedLive,
)
from .themeparks_errors import ThemeParksNotFoundError, ThemeParksSchemaError
from .themeparks_reference_data import AttractionMetadata

PROVIDER = DataSource.THEMEPARKS_WIKI

ENTITY_KINDS: Mapping[str, EntityKind] = {
    "ATTRACTION": EntityKind.ATTRACTION,
    "SHOW": EntityKind.SHOW,
    "RESTAURANT": EntityKind.RESTAURANT,
    "PARK": EntityKind.PARK,
}
CATALOG_KINDS = frozenset({EntityKind.ATTRACTION, EntityKind.SHOW})

# Showtime entry types in ``/live`` (captured 2026-09-24 and 2026-09-29).
PERFORMANCE_TIME = "Performance Time"
"""A scheduled performance: ``startTime == endTime``, a start a guest can plan around."""
NON_PERFORMANCE_SHOWTIME_TYPES = frozenset({"Operating", "Special Ticketed Event"})
"""Not show starts: ``Operating`` is an open window (a meet-and-greet line, e.g.
09:30-17:30) and ``Special Ticketed Event`` needs a separate party ticket."""


def check_timezone(payload: Mapping[str, Any]) -> None:
    """Fail closed when the payload declares a timezone other than the park's."""
    declared = payload.get("timezone")
    if declared is not None and declared != PARK_TZ.key:
        raise ThemeParksSchemaError(
            f"payload timezone {declared!r} is not the park timezone {PARK_TZ.key!r}"
        )


def parse_time(value: object, *, what: str) -> datetime:
    """An ISO-8601 provider time (``Z`` or an offset) as an aware ``PARK_TZ`` datetime."""
    if not isinstance(value, str):
        raise ThemeParksSchemaError(
            f"{what}: expected an ISO-8601 string, got {value!r}"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ThemeParksSchemaError(f"{what}: not an ISO-8601 time: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ThemeParksSchemaError(
            f"{what}: timestamp without a UTC offset: {value!r}"
        )
    return parsed.astimezone(PARK_TZ)


def parse_status(entity: Mapping[str, Any]) -> AttractionStatus:
    raw_status = entity.get("status")
    try:
        return AttractionStatus(raw_status)
    except ValueError as exc:
        raise ThemeParksSchemaError(
            f"unrecognized status {raw_status!r} for entity {entity.get('id')}"
        ) from exc


def entity_kind(entity: Mapping[str, Any]) -> EntityKind | None:
    return ENTITY_KINDS.get(entity.get("entityType"))  # type: ignore[arg-type]


def standby_wait(entity: Mapping[str, Any]) -> float | None:
    """The STANDBY wait, or ``None`` when there is no standby queue or no value.

    "Standby queues only for planning; other queue types kept raw" (P0-07):
    RETURN_TIME / PAID_RETURN_TIME are never read here. A wait that isn't a
    non-negative number (a string, a bool, an object) raises instead of being
    coerced.
    """
    queue = entity.get("queue") or {}
    if not isinstance(queue, Mapping):
        raise ThemeParksSchemaError(
            f"malformed queue for entity {entity.get('id')}: {queue!r}"
        )
    standby = queue.get("STANDBY")
    if standby is None:
        return None
    if not isinstance(standby, Mapping):
        raise ThemeParksSchemaError(
            f"malformed STANDBY queue for entity {entity.get('id')}: {standby!r}"
        )
    wait = standby.get("waitTime")
    if wait is None:
        return None
    # bool is an int subclass: True must not become a 1-minute wait.
    if isinstance(wait, bool) or not isinstance(wait, int | float) or wait < 0:
        raise ThemeParksSchemaError(
            f"malformed STANDBY waitTime for entity {entity.get('id')}: {wait!r}"
        )
    return float(wait)


def parse_showtimes(entity: Mapping[str, Any]) -> list[datetime]:
    """Scheduled show starts, in park time. Empty when the entity has none.

    Only ``Performance Time`` entries are starts. ``Operating`` windows and
    ``Special Ticketed Event`` times are dropped: reading a 09:30-17:30
    meet-and-greet window as "a show at 09:30" would make rule 3 demand arrival
    by 09:25, and party-only times are not attendable on a day ticket. Any other
    type (or none) is contract drift and raises, like an unknown status.
    """
    starts: list[datetime] = []
    try:
        for showtime in entity.get("showtimes") or []:
            kind = showtime.get("type")
            if kind == PERFORMANCE_TIME:
                starts.append(
                    parse_time(
                        showtime["startTime"], what=f"showtime of {entity.get('id')}"
                    )
                )
            elif kind not in NON_PERFORMANCE_SHOWTIME_TYPES:
                raise ThemeParksSchemaError(
                    f"unknown showtime type {kind!r} for entity {entity.get('id')}"
                )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ThemeParksSchemaError(
            f"malformed showtimes for entity {entity.get('id')}: {exc}"
        ) from exc
    return starts


def parse_forecast(entity: Mapping[str, Any]) -> list[tuple[datetime, float | None]]:
    """The provider's hourly standby forecast as ``(hour start, wait)`` pairs, sorted.

    Shape verified against the live API (2026-09-27 capture and 2026-10-01):
    ``forecast: [{"time": "...T09:00:00-04:00", "waitTime": 25, "percentage": 21}, ...]``,
    one point per park hour. ``percentage`` is not a wait and is not read. A
    missing ``forecast`` is ``[]`` (most entities have none at any given time); a
    null ``waitTime`` is kept as ``None`` (no reading for that hour). Anything
    else that isn't a non-negative number raises, like a malformed STANDBY wait.
    """
    points: list[tuple[datetime, float | None]] = []
    try:
        for point in entity.get("forecast") or []:
            start = parse_time(point["time"], what=f"forecast time of {entity.get('id')}")
            wait = point.get("waitTime")
            if wait is not None and (
                isinstance(wait, bool) or not isinstance(wait, int | float) or wait < 0
            ):
                raise ThemeParksSchemaError(
                    f"malformed forecast waitTime for entity {entity.get('id')}: {wait!r}"
                )
            points.append((start, None if wait is None else float(wait)))
    except (KeyError, TypeError, AttributeError) as exc:
        raise ThemeParksSchemaError(
            f"malformed forecast for entity {entity.get('id')}: {exc}"
        ) from exc
    return sorted(points, key=lambda p: p[0])


def index_entities(
    entities: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], list[MappingIssue]]:
    """Entities by provider id. A duplicated id is excluded entirely and reported."""
    entities = list(entities)
    ids: list[str] = []
    for entity in entities:
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id:
            raise ThemeParksSchemaError(
                f"entity without a string id: {entity.get('name')!r}"
            )
        ids.append(entity_id)

    counts = Counter(ids)
    issues = [
        MappingIssue(
            kind=IssueKind.DUPLICATE_PROVIDER_ID,
            provider=PROVIDER,
            provider_id=entity_id,
            entity_kind=None,
            detail=f"appears {count} times in one payload; every copy excluded",
        )
        for entity_id, count in counts.items()
        if count > 1
    ]
    index = {
        entity_id: entity
        for entity_id, entity in zip(ids, entities, strict=True)
        if counts[entity_id] == 1
    }
    return index, issues


def parse_catalog(
    payload: Mapping[str, Any],
    metadata: Mapping[str, AttractionMetadata],
    *,
    excluded: Collection[str] = frozenset(),
) -> NormalizedCatalog:
    """``/entity/{park}/children`` -> ``Attraction``s for ATTRACTION and SHOW entities.

    Entities of other kinds (restaurants, the park itself) are not catalog
    items and are skipped without an issue, and so are the ``excluded`` ids
    (entities left out on purpose, e.g. party-only entertainment). Any other
    entity without curated metadata is reported as ``MISSING_METADATA``.
    Payload order is preserved.
    """
    check_timezone(payload)
    index, issues = index_entities(payload.get("children", []))

    attractions: list[Attraction] = []
    kinds: dict[str, EntityKind] = {}
    for entity_id, entity in index.items():
        kind = entity_kind(entity)
        if kind is None:
            issues.append(
                _issue(IssueKind.UNKNOWN_ENTITY_KIND, entity_id, None, entity)
            )
            continue
        if kind not in CATALOG_KINDS or entity_id in excluded:
            continue
        curated = metadata.get(entity_id)
        if curated is None:
            issues.append(_issue(IssueKind.MISSING_METADATA, entity_id, kind, entity))
            continue
        try:
            attractions.append(
                Attraction(
                    node_id=entity_id,
                    name=entity["name"],
                    category=curated["category"],
                    height_restriction_cm=curated["height_restriction_cm"],
                    typical_wait_minutes=curated["typical_wait_minutes"],
                    outdoor=curated["outdoor"],
                    land=curated["land"],
                )
            )
            kinds[entity_id] = kind
        except KeyError as exc:
            raise ThemeParksSchemaError(
                f"malformed catalog entity {entity_id!r}: missing {exc}"
            ) from exc
    return NormalizedCatalog(attractions=attractions, kinds=kinds, issues=issues)


def parse_live(payload: Mapping[str, Any]) -> NormalizedLive:
    """``/entity/{park}/live`` -> one canonical ``LiveEntity`` per non-park entity.

    Eager and strict: every entity's status and times are parsed, so an unknown
    status anywhere raises. ``ThemeParksClient`` parses lazily instead, only for
    the ids it is asked about.
    """
    check_timezone(payload)
    index, issues = index_entities(payload.get("liveData", []))

    entities: dict[str, LiveEntity] = {}
    for entity_id, entity in index.items():
        kind = entity_kind(entity)
        if kind is None:
            issues.append(
                _issue(IssueKind.UNKNOWN_ENTITY_KIND, entity_id, None, entity)
            )
            continue
        if kind is EntityKind.PARK:
            continue  # the park reports itself in liveData; it is not a plannable node
        last_updated = entity.get("lastUpdated")
        entities[entity_id] = LiveEntity(
            entity_id=entity_id,
            kind=kind,
            status=parse_status(entity),
            standby_wait_minutes=standby_wait(entity),
            showtimes=tuple(parse_showtimes(entity)),
            observed_at=(
                None
                if last_updated is None
                else parse_time(last_updated, what=f"lastUpdated of {entity_id}")
            ),
        )
    return NormalizedLive(entities=entities, issues=issues)


def parse_schedule(
    payload: Mapping[str, Any],
    on_date: date,
    *,
    park_id: str,
    park_name: str,
    park_outdoor: bool,
) -> Park:
    """``/entity/{park}/schedule`` -> the ``Park`` operating window on ``on_date``.

    No OPERATING entry for the date raises ``ThemeParksNotFoundError``; more than
    one is ambiguous and raises ``ThemeParksSchemaError``.
    """
    check_timezone(payload)
    matches = [
        entry
        for entry in payload.get("schedule", [])
        if entry.get("date") == on_date.isoformat() and entry.get("type") == "OPERATING"
    ]
    if not matches:
        raise ThemeParksNotFoundError(
            f"no OPERATING schedule entry for park {park_id} on {on_date.isoformat()}"
        )
    if len(matches) > 1:
        raise ThemeParksSchemaError(
            f"ambiguous schedule: {len(matches)} OPERATING entries for park "
            f"{park_id} on {on_date.isoformat()}"
        )

    entry = matches[0]
    what = f"schedule entry for park {park_id} on {on_date.isoformat()}"
    try:
        opening_time = parse_time(entry["openingTime"], what=what)
        closing_time = parse_time(entry["closingTime"], what=what)
    except KeyError as exc:
        raise ThemeParksSchemaError(f"malformed {what}: missing {exc}") from exc

    return Park(
        park_id=park_id,
        name=park_name,
        opening_time=opening_time,
        closing_time=closing_time,
        outdoor=park_outdoor,
    )


def _issue(
    kind: IssueKind,
    entity_id: str,
    entity_kind_: EntityKind | None,
    entity: Mapping[str, Any],
) -> MappingIssue:
    return MappingIssue(
        kind=kind,
        provider=PROVIDER,
        provider_id=entity_id,
        entity_kind=entity_kind_,
        detail=f"{entity.get('entityType')} {entity.get('name')!r}",
    )
