"""Shared pytest fixtures. Add fakes for the clients (ThemeParksClient,
OpenMeteoClient, InMemoryKnowledgeStore) here once the first one exists, so
unit/ tests never need real network/DB access. The Postgres repositories are
tested against a real database in tests/integration/postgres/."""

from datetime import date, datetime, timedelta

import httpx
import pytest

from parkmind.core.contracts import (
    PARK_TZ,
    Attraction,
    AttractionCategory,
    AttractionStatus,
    Park,
    WaitEstimate,
    WeatherHour,
)
from parkmind.services.clients.open_meteo_client import OpenMeteoClient

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


@pytest.fixture
def fake_weather_hours() -> list[WeatherHour]:
    """Generates 24 hours of mock WeatherHour domain models for testing."""
    base_time = datetime(2026, 9, 17, 0, 0, tzinfo=PARK_TZ)
    hours: list[WeatherHour] = []
    for i in range(24):
        ts = base_time + timedelta(hours=i)
        # Afternoon rain simulation around 14:00-16:00
        is_rain = 14 <= ts.hour <= 16
        hours.append(
            WeatherHour(
                timestamp=ts,
                condition="rain" if is_rain else "clear",
                temperature_f=70.0 + (i if i <= 14 else 28 - i) * 1.2,
                precipitation_probability=0.85 if is_rain else 0.1,
            )
        )
    return hours


@pytest.fixture
def fake_open_meteo_client(fake_weather_hours: list[WeatherHour]) -> OpenMeteoClient:
    """Fake OpenMeteoClient providing deterministic weather data via mock HTTP transport."""
    mock_payload = {
        "latitude": 28.4177,
        "longitude": -81.5812,
        "timezone": "America/New_York",
        "hourly": {
            "time": [
                h.timestamp.strftime("%Y-%m-%dT%H:%M") for h in fake_weather_hours
            ],
            "temperature_2m": [h.temperature_f for h in fake_weather_hours],
            "precipitation_probability": [
                int(h.precipitation_probability * 100) for h in fake_weather_hours
            ],
            "weather_code": [
                61 if h.condition == "rain" else 0 for h in fake_weather_hours
            ],
        },
    }

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=mock_payload))
    return OpenMeteoClient(transport=transport)