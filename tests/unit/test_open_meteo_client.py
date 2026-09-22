"""Unit tests for OpenMeteoClient adapter."""

from datetime import date, datetime
from typing import Any

import httpx
import pytest

from parkmind.core.contracts import PARK_TZ, WeatherHour
from parkmind.services.clients import open_meteo_client
from parkmind.services.clients.open_meteo_client import (
    OpenMeteoClient,
    OpenMeteoClientError,
    OpenMeteoNotFoundError,
    OpenMeteoSchemaError,
    OpenMeteoUnavailableError,
    _map_wmo_code_to_condition,
)
from parkmind.services.ports import WeatherPort


def _accepts_port(port: WeatherPort) -> None:
    """mypy-only check that OpenMeteoClient satisfies WeatherPort structurally."""


@pytest.fixture
def mock_normal_weather_response() -> dict[str, Any]:
    return {
        "latitude": 28.4177,
        "longitude": -81.5812,
        "generationtime_ms": 0.12,
        "utc_offset_seconds": -14400,
        "timezone": "America/New_York",
        "timezone_abbreviation": "EDT",
        "elevation": 30.0,
        "hourly_units": {
            "time": "iso8601",
            "temperature_2m": "°F",
            "precipitation_probability": "%",
            "weather_code": "wmo code",
        },
        "hourly": {
            "time": [
                "2026-09-17T09:00",
                "2026-09-17T10:00",
                "2026-09-17T11:00",
                "2026-09-17T12:00",
                "2026-09-17T13:00",
                "2026-09-17T14:00",
            ],
            "temperature_2m": [74.5, 78.1, 82.3, 85.0, 86.2, 84.1],
            "precipitation_probability": [0, 10, 20, 45, 80, 75],
            "weather_code": [0, 1, 2, 3, 61, 80],
        },
    }


# ============================================================================
# INITIALIZATION & CONTEXT MANAGER TESTS
# ============================================================================


def test_default_initialization():
    """Verify default base_url, retries, and coordinates."""
    adapter = OpenMeteoClient()
    assert adapter.base_url == "https://api.open-meteo.com/v1"
    assert adapter.max_retries == 3
    assert adapter.backoff_seconds == 0.5
    assert adapter.default_latitude == 28.4177
    assert adapter.default_longitude == -81.5812
    adapter.close()


def test_custom_initialization():
    """Verify custom parameters are honored."""
    adapter = OpenMeteoClient(
        base_url="https://custom.api.test/v1/",
        max_retries=5,
        backoff_seconds=1.0,
        default_latitude=30.0,
        default_longitude=-80.0,
    )
    assert adapter.base_url == "https://custom.api.test/v1"
    assert adapter.max_retries == 5
    assert adapter.backoff_seconds == 1.0
    assert adapter.default_latitude == 30.0
    assert adapter.default_longitude == -80.0
    adapter.close()


def test_max_retries_zero_rejected_at_construction():
    """Verify max_retries < 1 raises ValueError."""
    with pytest.raises(ValueError, match="max_retries"):
        OpenMeteoClient(max_retries=0)


def test_context_manager_closes_client():
    """Verify context manager calls close() on exit."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "hourly": {
                    "time": [],
                    "temperature_2m": [],
                    "precipitation_probability": [],
                    "weather_code": [],
                }
            },
        )
    )
    with OpenMeteoClient(transport=transport) as adapter:
        assert not adapter._client.is_closed
    assert adapter._client.is_closed


# ============================================================================
# STRUCTURAL PROTOCOL & MAPPING TESTS
# ============================================================================


def test_open_meteo_client_implements_weather_port_protocol():
    """Verify OpenMeteoClient satisfies WeatherPort protocol structurally."""
    with OpenMeteoClient() as adapter:
        _accepts_port(adapter)


def test_get_hourly_forecast_successful_mapping(mock_normal_weather_response):
    """Verify JSON response maps cleanly to WeatherHour domain models."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=mock_normal_weather_response)
    )
    adapter = OpenMeteoClient(transport=transport)

    hours = adapter.get_hourly_forecast(forecast_days=1)

    assert len(hours) == 6
    first = hours[0]
    assert isinstance(first, WeatherHour)
    assert first.timestamp.tzinfo == PARK_TZ
    assert first.timestamp.hour == 9
    assert first.temperature_f == 74.5
    assert first.precipitation_probability == 0.0
    assert first.condition == "clear"

    rain_hour = hours[4]
    assert rain_hour.temperature_f == 86.2
    assert rain_hour.precipitation_probability == 0.8
    assert rain_hour.condition == "rain"


def test_get_hourly_forecast_timezone_normalization():
    """Verify naive and aware timestamps normalize to America/New_York (PARK_TZ)."""
    mock_payload = {
        "hourly": {
            "time": ["2026-09-17T12:00:00+00:00", "2026-09-17T13:00"],
            "temperature_2m": [70.0, 72.0],
            "precipitation_probability": [10, 10],
            "weather_code": [0, 0],
        }
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=mock_payload))
    adapter = OpenMeteoClient(transport=transport)

    hours = adapter.get_hourly_forecast()
    assert len(hours) == 2
    for h in hours:
        assert h.timestamp.tzinfo == PARK_TZ


def test_wmo_code_mapping_conditions():
    """Verify standard WMO weather codes map to expected condition strings."""
    assert _map_wmo_code_to_condition(0) == "clear"
    assert _map_wmo_code_to_condition(1) == "partly_cloudy"
    assert _map_wmo_code_to_condition(2) == "partly_cloudy"
    assert _map_wmo_code_to_condition(3) == "cloudy"
    assert _map_wmo_code_to_condition(45) == "fog"
    assert _map_wmo_code_to_condition(51) == "drizzle"
    assert _map_wmo_code_to_condition(61) == "rain"
    assert _map_wmo_code_to_condition(71) == "snow"
    assert _map_wmo_code_to_condition(80) == "rain"
    assert _map_wmo_code_to_condition(95) == "storm"
    assert _map_wmo_code_to_condition(99) == "storm"
    assert _map_wmo_code_to_condition(999) == "unknown"


def test_date_range_parameters():
    """Verify start_date and end_date parameters are formatted and sent in request."""
    captured_params = {}

    def capture_handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-09-18T10:00"],
                    "temperature_2m": [80.0],
                    "precipitation_probability": [20],
                    "weather_code": [0],
                }
            },
        )

    transport = httpx.MockTransport(capture_handler)
    adapter = OpenMeteoClient(transport=transport)

    start = date(2026, 9, 18)
    end = date(2026, 9, 19)
    result = adapter.get_hourly_forecast(start_date=start, end_date=end)

    assert len(result) == 1
    assert captured_params.get("start_date") == "2026-09-18"
    assert captured_params.get("end_date") == "2026-09-19"


def test_start_date_without_end_date_defaults_end_date():
    """Verify passing only start_date defaults end_date to start_date to avoid 400 Bad Request."""
    captured_params = {}

    def capture_handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-09-18T10:00"],
                    "temperature_2m": [80.0],
                    "precipitation_probability": [20],
                    "weather_code": [0],
                }
            },
        )

    transport = httpx.MockTransport(capture_handler)
    adapter = OpenMeteoClient(transport=transport)

    start = date(2026, 9, 18)
    adapter.get_hourly_forecast(start_date=start)

    assert captured_params.get("start_date") == "2026-09-18"
    assert captured_params.get("end_date") == "2026-09-18"


def test_get_weather_explicit_timestamp(mock_normal_weather_response):
    """Verify get_weather method returns closest WeatherHour for explicit datetime."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=mock_normal_weather_response)
    )
    adapter = OpenMeteoClient(transport=transport)

    target_time = datetime(2026, 9, 17, 11, 15, tzinfo=PARK_TZ)
    weather = adapter.get_weather(at_time=target_time)

    assert isinstance(weather, WeatherHour)
    # Closest hour is 11:00
    assert weather.timestamp == datetime(2026, 9, 17, 11, 0, tzinfo=PARK_TZ)
    assert weather.temperature_f == 82.3


def test_get_weather_raises_when_date_out_of_range():
    """Verify get_weather raises OpenMeteoNotFoundError if date query yields empty forecast."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "hourly": {
                    "time": [],
                    "temperature_2m": [],
                    "precipitation_probability": [],
                    "weather_code": [],
                }
            },
        )
    )
    adapter = OpenMeteoClient(transport=transport)

    future_time = datetime(2026, 12, 1, 12, 0, tzinfo=PARK_TZ)
    with pytest.raises(OpenMeteoNotFoundError) as exc_info:
        adapter.get_weather(at_time=future_time)

    assert "no hourly weather forecast data available" in str(exc_info.value).lower()


# ============================================================================
# RETRY & RESILIENCE TESTS
# ============================================================================


def test_retry_on_503_recovers_on_second_attempt(mock_normal_weather_response):
    """Verify bounded retry recovers when first attempt returns 503 and second succeeds."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json=mock_normal_weather_response)

    transport = httpx.MockTransport(handler)
    adapter = OpenMeteoClient(transport=transport, max_retries=3, backoff_seconds=0.01)

    result = adapter.get_hourly_forecast()
    assert len(result) == 6
    assert call_count == 2


def test_retry_on_429_rate_limit_recovers(mock_normal_weather_response):
    """Verify bounded retry handles 429 rate limit and recovers."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, json=mock_normal_weather_response)

    transport = httpx.MockTransport(handler)
    adapter = OpenMeteoClient(transport=transport, max_retries=3, backoff_seconds=0.01)

    result = adapter.get_hourly_forecast()
    assert len(result) == 6
    assert call_count == 2


def test_retry_exhaustion_raises_unavailable_error():
    """Verify 3 consecutive 500 responses exhaust retries and raise OpenMeteoUnavailableError."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    adapter = OpenMeteoClient(transport=transport, max_retries=3, backoff_seconds=0.01)

    with pytest.raises(OpenMeteoUnavailableError) as exc_info:
        adapter.get_hourly_forecast()

    assert call_count == 3
    assert exc_info.value.status_code == 500
    assert "after 3 attempts" in str(exc_info.value)


def test_retry_on_timeout_exhaustion():
    """Verify repeated timeouts exhaust retries and raise OpenMeteoUnavailableError."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectTimeout("Connection timed out")

    transport = httpx.MockTransport(handler)
    adapter = OpenMeteoClient(transport=transport, max_retries=3, backoff_seconds=0.01)

    with pytest.raises(OpenMeteoUnavailableError) as exc_info:
        adapter.get_hourly_forecast()

    assert call_count == 3
    assert "failed after 3 attempts" in str(exc_info.value)


def test_backoff_is_linear_and_only_between_attempts(monkeypatch):
    """Pins the schedule documented in the module docstring: 3 attempts,
    0.5s then 1.0s, and no sleep after the final attempt."""
    sleeps: list[float] = []
    monkeypatch.setattr(open_meteo_client.time, "sleep", sleeps.append)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503)

    adapter = OpenMeteoClient(
        transport=httpx.MockTransport(handler),
        max_retries=3,
        backoff_seconds=0.5,
    )
    with pytest.raises(OpenMeteoUnavailableError):
        adapter.get_hourly_forecast()

    assert calls["count"] == 3
    assert sleeps == [0.5, 1.0]


def test_redirect_raises_schema_error_not_retried():
    """Verify 3xx redirect fails closed without retry and raises OpenMeteoSchemaError."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(301, headers={"location": "/v2/forecast"})

    adapter = OpenMeteoClient(
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )
    with pytest.raises(OpenMeteoSchemaError):
        adapter.get_hourly_forecast()

    assert calls["count"] == 1


def test_unexpected_4xx_is_not_retried():
    """Verify 400 error is not retried."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, json={"error": True, "reason": "Bad Request"})

    adapter = OpenMeteoClient(
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )
    with pytest.raises(OpenMeteoClientError):
        adapter.get_hourly_forecast()

    assert calls["count"] == 1


# ============================================================================
# ERROR CONTRACT & PAYLOAD TESTS
# ============================================================================


def test_http_400_with_real_open_meteo_reason():
    """Verify HTTP 400 error payload parsing includes Open-Meteo's 'reason' in exception."""
    error_payload = {
        "error": True,
        "reason": "Latitude must be in range of -90 to 90. Given: 95.0",
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(400, json=error_payload))
    adapter = OpenMeteoClient(transport=transport, max_retries=1)

    with pytest.raises(OpenMeteoClientError) as exc_info:
        adapter.get_hourly_forecast(latitude=95.0)

    assert exc_info.value.status_code == 400
    assert "Latitude must be in range of -90 to 90" in str(exc_info.value)


def test_malformed_json_response_raises_schema_error():
    """Verify non-JSON response raises OpenMeteoSchemaError."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text="<html>Error</html>")
    )
    adapter = OpenMeteoClient(transport=transport, max_retries=1)

    with pytest.raises(OpenMeteoSchemaError) as exc_info:
        adapter.get_hourly_forecast()

    assert (
        "malformed" in str(exc_info.value).lower()
        or "json" in str(exc_info.value).lower()
    )


def test_missing_hourly_field_raises_schema_error():
    """Verify payload missing 'hourly' section raises OpenMeteoSchemaError."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"some_other_key": 123})
    )
    adapter = OpenMeteoClient(transport=transport, max_retries=1)

    with pytest.raises(OpenMeteoSchemaError) as exc_info:
        adapter.get_hourly_forecast()

    assert "hourly" in str(exc_info.value).lower()


def test_mismatched_array_lengths_raises_schema_error():
    """Verify payload with mismatched array lengths raises OpenMeteoSchemaError."""
    payload = {
        "hourly": {
            "time": ["2026-09-17T09:00", "2026-09-17T10:00"],
            "temperature_2m": [74.5],  # only 1 item
            "precipitation_probability": [0, 10],
            "weather_code": [0, 1],
        }
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    adapter = OpenMeteoClient(transport=transport, max_retries=1)

    with pytest.raises(OpenMeteoSchemaError) as exc_info:
        adapter.get_hourly_forecast()

    assert (
        "mismatched" in str(exc_info.value).lower()
        or "length" in str(exc_info.value).lower()
    )
