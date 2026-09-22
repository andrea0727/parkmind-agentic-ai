"""Shared pytest fixtures. Add fakes for the clients (ThemeParksClient,
OpenMeteoClient, InMemoryKnowledgeStore) here once the first one exists, so
unit/ tests never need real network/DB access. The Postgres repositories are
tested against a real database in tests/integration/postgres/."""

from datetime import date, datetime

from parkmind.core.contracts import (
    Attraction,
    AttractionCategory,
    AttractionStatus,
    Park,
    WaitEstimate,
)
from parkmind.core.contracts.base import PARK_TZ

SPACE_MOUNTAIN_ID = "b2260923-9315-40fd-9c6b-44dd811dbe64"
MAGIC_KINGDOM_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"


class FakeThemeParksClient:
    """Canned ParkDataPort implementation — no HTTP, no fixtures on disk.
    For tests one layer up (use_cases, graph nodes) that just need *a*
    working ParkDataPort and don't care about ThemeParksClient's own
    parsing logic (that's tests/unit/test_themeparks_client.py's job)."""

    def get_catalog(self) -> list[Attraction]:
        return [
            Attraction(
                node_id=SPACE_MOUNTAIN_ID,
                name="Space Mountain",
                category=AttractionCategory.THRILL,
                height_restriction_cm=112,
                typical_wait_minutes=60,
                outdoor=False,
            )
        ]

    def get_schedule(self, on_date: date) -> Park:
        return Park(
            park_id=MAGIC_KINGDOM_ID,
            name="Magic Kingdom Park",
            opening_time=datetime(on_date.year, on_date.month, on_date.day, 9, 0, tzinfo=PARK_TZ),
            closing_time=datetime(on_date.year, on_date.month, on_date.day, 22, 0, tzinfo=PARK_TZ),
            outdoor=True,
        )

    def get_live_waits(self, attraction_ids: list[str]) -> dict[str, WaitEstimate]:
        return {
            attraction_id: WaitEstimate(
                attraction_id=attraction_id, wait_minutes=30, status=AttractionStatus.OPERATING
            )
            for attraction_id in attraction_ids
            if attraction_id == SPACE_MOUNTAIN_ID
        }

    def get_attraction_status(self, attraction_ids: list[str]) -> dict[str, AttractionStatus]:
        return {
            attraction_id: AttractionStatus.OPERATING
            for attraction_id in attraction_ids
            if attraction_id == SPACE_MOUNTAIN_ID
        }

    def get_showtimes(self, attraction_ids: list[str]) -> dict[str, list[datetime]]:
        return {}
