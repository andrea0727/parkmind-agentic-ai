"""P0-11: both adapters expose the raw payload, and parsing it is a pure function.

The snapshot collector stores raw provider payloads verbatim (Architecture 41,
C21) and must be able to re-normalize them later without HTTP. Fixtures are the
real responses captured together on 2026-09-27 (tests/fixtures/snapshot_2026-09-27).
"""

import copy
import json
from pathlib import Path

import httpx
import pytest

from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.open_meteo_client import (
    OpenMeteoClient,
    OpenMeteoSchemaError,
)
from parkmind.services.clients.open_meteo_normalize import parse_hourly_forecast
from parkmind.services.clients.themeparks_client import (
    ThemeParksClient,
    ThemeParksSchemaError,
)

CAPTURE = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_2026-09-27"
PARK_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"


def _capture(name: str) -> dict:
    return json.loads((CAPTURE / name).read_text(encoding="utf-8"))


def _serving(body: dict) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, json=body))


# ----------------------------------------------------------------- Open-Meteo


def test_real_capture_parses_to_park_local_hours() -> None:
    hours = parse_hourly_forecast(_capture("open_meteo_hourly.json"))

    assert len(hours) == 24
    assert all(h.timestamp.tzinfo is PARK_TZ for h in hours)
    assert hours[0].timestamp.isoformat() == "2026-09-27T00:00:00-04:00"
    assert all(0.0 <= h.precipitation_probability <= 1.0 for h in hours)


def test_fetch_hourly_payload_returns_the_provider_json_verbatim() -> None:
    payload = _capture("open_meteo_hourly.json")
    client = OpenMeteoClient(transport=_serving(payload), backoff_seconds=0)

    raw = client.fetch_hourly_payload(start_date="2026-09-27", end_date="2026-09-27")

    assert raw == payload
    assert client.get_hourly_forecast(start_date="2026-09-27") == parse_hourly_forecast(payload)


def test_stored_payload_in_another_timezone_is_rejected() -> None:
    """Naive hourly times are park-local only because the request asked for it."""
    payload = _capture("open_meteo_hourly.json")
    payload["timezone"] = "UTC"

    with pytest.raises(OpenMeteoSchemaError, match="timezone"):
        parse_hourly_forecast(payload)


def test_stored_payload_in_celsius_is_rejected() -> None:
    payload = _capture("open_meteo_hourly.json")
    payload["hourly_units"]["temperature_2m"] = "°C"

    with pytest.raises(OpenMeteoSchemaError, match="Fahrenheit"):
        parse_hourly_forecast(payload)


# ------------------------------------------------------------------ ThemeParks


def test_fetch_live_payload_returns_the_provider_json_verbatim() -> None:
    payload = _capture("themeparks_live.json")
    client = ThemeParksClient(PARK_ID, base_url="https://testserver", transport=_serving(payload))

    assert client.fetch_live_payload() == payload


def test_fetch_schedule_payload_returns_the_provider_json_verbatim() -> None:
    payload = _capture("themeparks_schedule.json")
    client = ThemeParksClient(PARK_ID, base_url="https://testserver", transport=_serving(payload))

    assert client.fetch_schedule_payload() == payload


def test_raw_fetch_still_rejects_a_foreign_timezone() -> None:
    payload = copy.deepcopy(_capture("themeparks_live.json"))
    payload["timezone"] = "Europe/Paris"
    client = ThemeParksClient(PARK_ID, base_url="https://testserver", transport=_serving(payload))

    with pytest.raises(ThemeParksSchemaError):
        client.fetch_live_payload()
