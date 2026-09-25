"""P0-10: canonical status/timestamps and surfaced (never merged) identity problems.

Pure functions over the captured ThemeParks payload shapes in
tests/fixtures/themeparks/ -- no HTTP, no network.
"""

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from parkmind.core.contracts import AttractionCategory, AttractionStatus, DataSource
from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.normalization import IssueKind
from parkmind.services.clients.themeparks_client import (
    ThemeParksNotFoundError,
    ThemeParksSchemaError,
)
from parkmind.services.clients.themeparks_normalize import (
    parse_catalog,
    parse_live,
    parse_schedule,
    parse_time,
)
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
)
from parkmind.services.ports import EntityKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "themeparks"
PARK_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"
SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"
TRON = "5a43d1a7-ad53-4d25-abfe-25625f0da304"
JUNGLE_CRUISE = "796b0a25-c51e-456e-9bb8-50a324e301b3"
FRIENDSHIP_FAIRE = "4c31b3ad-5dc9-437f-ac1a-0fdff36a2818"  # SHOW
CRYSTAL_PALACE = "c4b64a31-e855-46a9-9d1a-aa13c99e88af"  # RESTAURANT


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------ canonical status


def test_all_provider_statuses_map_to_the_enum() -> None:
    live = parse_live(_fixture("live_magic_kingdom.json"))

    assert {e.status for e in live.entities.values()} == set(AttractionStatus)
    assert live.entities[SPACE_MOUNTAIN].status is AttractionStatus.OPERATING
    assert live.entities[BIG_THUNDER].status is AttractionStatus.DOWN
    assert live.entities[TRON].status is AttractionStatus.REFURBISHMENT
    assert live.entities[JUNGLE_CRUISE].status is AttractionStatus.CLOSED


def test_unknown_status_raises_schema_error() -> None:
    with pytest.raises(ThemeParksSchemaError):
        parse_live(_fixture("live_magic_kingdom_unknown_status.json"))


# --------------------------------------------------------- canonical timestamps


@pytest.mark.parametrize(
    "raw",
    ["2026-09-16T14:00:00Z", "2026-09-16T14:00:00+00:00", "2026-09-16T10:00:00-04:00"],
)
def test_z_and_offset_timestamps_become_park_tz(raw: str) -> None:
    parsed = parse_time(raw, what="test")

    assert parsed.tzinfo is PARK_TZ
    assert parsed == datetime(2026, 9, 16, 10, 0, tzinfo=PARK_TZ)


def test_timestamp_without_offset_is_rejected_not_read_as_local_time() -> None:
    with pytest.raises(ThemeParksSchemaError):
        parse_time("2026-09-16T10:00:00", what="test")


@pytest.mark.parametrize("raw", ["yesterday", None, 1726495200])
def test_non_iso_time_raises_schema_error(raw: object) -> None:
    with pytest.raises(ThemeParksSchemaError):
        parse_time(raw, what="test")


def test_last_updated_is_canonical_observed_at() -> None:
    live = parse_live(_fixture("live_magic_kingdom.json"))

    observed = live.entities[SPACE_MOUNTAIN].observed_at
    # 13:36:04.087Z is 09:36:04.087 in New York (EDT).
    assert observed == datetime(2026, 9, 16, 9, 36, 4, 87000, tzinfo=PARK_TZ)
    assert observed is not None and observed.tzinfo is PARK_TZ


def test_showtimes_are_park_local() -> None:
    live = parse_live(_fixture("live_magic_kingdom.json"))

    showtimes = live.entities[FRIENDSHIP_FAIRE].showtimes
    assert [t.hour for t in showtimes] == [10, 11]
    assert all(t.tzinfo is PARK_TZ for t in showtimes)


def test_foreign_timezone_raises_schema_error() -> None:
    with pytest.raises(ThemeParksSchemaError):
        parse_live(_fixture("live_magic_kingdom_other_timezone.json"))


def test_the_park_timezone_is_accepted_even_as_an_equal_zone() -> None:
    payload = _fixture("live_magic_kingdom.json")

    assert payload["timezone"] == ZoneInfo("America/New_York").key
    parse_live(payload)  # does not raise


# ------------------------------------------------------------------ live shape


def test_live_keeps_standby_only_and_excludes_the_park_itself() -> None:
    live = parse_live(_fixture("live_magic_kingdom.json"))

    assert PARK_ID not in live.entities
    assert live.entities[SPACE_MOUNTAIN].standby_wait_minutes == 60
    # Jungle Cruise only has a RETURN_TIME queue: never read.
    assert live.entities[JUNGLE_CRUISE].standby_wait_minutes is None
    assert live.entities[CRYSTAL_PALACE].kind is EntityKind.RESTAURANT
    assert live.issues == []


# ---------------------------------------------- surfaced, never silently merged


def test_duplicate_provider_id_excludes_every_copy_and_reports_it() -> None:
    live = parse_live(_fixture("live_magic_kingdom_duplicate_id.json"))

    # Two different readings (60 vs 95 min) for one id: neither is trusted.
    assert SPACE_MOUNTAIN not in live.entities
    assert [(i.kind, i.provider_id, i.provider) for i in live.issues] == [
        (IssueKind.DUPLICATE_PROVIDER_ID, SPACE_MOUNTAIN, DataSource.THEMEPARKS_WIKI)
    ]
    # Everything else still normalizes.
    assert BIG_THUNDER in live.entities


def test_catalog_entity_without_metadata_is_reported() -> None:
    catalog = parse_catalog(_fixture("children_magic_kingdom.json"), MAGIC_KINGDOM_ATTRACTION_METADATA)

    reported = {(i.kind, i.provider_id, i.entity_kind) for i in catalog.issues}
    assert reported == {
        (IssueKind.MISSING_METADATA, "unknown-attraction-not-in-metadata-table", EntityKind.ATTRACTION),
        (IssueKind.MISSING_METADATA, FRIENDSHIP_FAIRE, EntityKind.SHOW),
    }
    assert {a.node_id for a in catalog.attractions} == {SPACE_MOUNTAIN, BIG_THUNDER, TRON, JUNGLE_CRUISE}


def test_restaurants_and_the_park_are_not_catalog_items_and_not_issues() -> None:
    catalog = parse_catalog(_fixture("children_magic_kingdom.json"), MAGIC_KINGDOM_ATTRACTION_METADATA)

    ids = {a.node_id for a in catalog.attractions} | {i.provider_id for i in catalog.issues}
    assert CRYSTAL_PALACE not in ids
    assert PARK_ID not in ids


def test_unknown_entity_type_is_reported() -> None:
    payload = _fixture("children_magic_kingdom.json")
    payload["children"].append(
        {"id": "merch-1", "name": "Emporium", "entityType": "MERCHANDISE", "parentId": PARK_ID}
    )

    catalog = parse_catalog(payload, MAGIC_KINGDOM_ATTRACTION_METADATA)

    assert (IssueKind.UNKNOWN_ENTITY_KIND, "merch-1") in {
        (i.kind, i.provider_id) for i in catalog.issues
    }


def test_duplicate_catalog_id_is_excluded_even_when_curated() -> None:
    payload = _fixture("children_magic_kingdom.json")
    payload["children"].append(
        {"id": SPACE_MOUNTAIN, "name": "Space Mountain (again)", "entityType": "ATTRACTION"}
    )

    catalog = parse_catalog(payload, MAGIC_KINGDOM_ATTRACTION_METADATA)

    assert SPACE_MOUNTAIN not in {a.node_id for a in catalog.attractions}
    assert (IssueKind.DUPLICATE_PROVIDER_ID, SPACE_MOUNTAIN) in {
        (i.kind, i.provider_id) for i in catalog.issues
    }


def test_catalog_fields_come_from_curated_metadata() -> None:
    catalog = parse_catalog(_fixture("children_magic_kingdom.json"), MAGIC_KINGDOM_ATTRACTION_METADATA)

    space = next(a for a in catalog.attractions if a.node_id == SPACE_MOUNTAIN)
    assert (space.category, space.height_restriction_cm) == (AttractionCategory.THRILL, 112)


def test_entity_without_an_id_raises_schema_error() -> None:
    payload = _fixture("live_magic_kingdom.json")
    payload["liveData"].append({"name": "ghost", "entityType": "ATTRACTION", "status": "OPERATING"})

    with pytest.raises(ThemeParksSchemaError):
        parse_live(payload)


# ------------------------------------------------------------------- schedule


def test_schedule_times_are_park_local() -> None:
    park = parse_schedule(
        _fixture("schedule_magic_kingdom_2026-09-16.json"),
        date(2026, 9, 16),
        park_id=PARK_ID,
        park_name="Magic Kingdom Park",
        park_outdoor=True,
    )

    assert park.opening_time.tzinfo is PARK_TZ and park.closing_time.tzinfo is PARK_TZ


def test_schedule_without_a_matching_day_is_not_found() -> None:
    with pytest.raises(ThemeParksNotFoundError):
        parse_schedule(
            _fixture("schedule_magic_kingdom_2026-09-16.json"),
            date(2031, 1, 1),
            park_id=PARK_ID,
            park_name="Magic Kingdom Park",
            park_outdoor=True,
        )
