"""Shared pytest fixtures.

Fakes for clients (OpenMeteoClient, ThemeParksClient, InMemoryKnowledgeStore,
PostgresRepository) are registered here so unit/integration tests never need real
network/DB access.
"""

from datetime import datetime, timedelta

import httpx
import pytest

from parkmind.core.contracts import PARK_TZ, WeatherHour
from parkmind.services.clients.open_meteo_client import OpenMeteoClient


@pytest.fixture
def fake_weather_hours() -> list[WeatherHour]:
    """Generates 24 hours of mock WeatherHour domain models for testing."""
    base_time = datetime(2026, 9, 17, 8, 0, tzinfo=PARK_TZ)
    hours: list[WeatherHour] = []
    for i in range(12):
        ts = base_time + timedelta(hours=i)
        # Afternoon rain simulation around 14:00-16:00
        is_rain = 14 <= ts.hour <= 16
        hours.append(
            WeatherHour(
                timestamp=ts,
                condition="rain" if is_rain else "clear",
                temperature_f=75.0 + i * 1.5,
                precipitation_probability=0.85 if is_rain else 0.1,
            )
        )
    return hours


@pytest.fixture
def fake_open_meteo_client(fake_weather_hours) -> OpenMeteoClient:
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
    return OpenMeteoClient(http_client=httpx.Client(transport=transport))
