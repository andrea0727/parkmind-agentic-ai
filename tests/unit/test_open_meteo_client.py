"""
Unit tests for OpenMeteoClient adapter.

Tests:
1. Normal response mapping to list[WeatherHour]
2. Temperature in Fahrenheit mapping
3. Precipitation probability normalization (0-100% -> 0.0-1.0)
4. Timezone normalization to America/New_York (PARK_TZ)
5. WMO weather code interpretation
6. Recoverable error handling on HTTP 5xx/4xx, timeouts, and malformed responses
"""

from datetime import date, datetime

import httpx
import pytest

from parkmind.core.contracts import PARK_TZ, WeatherHour
from parkmind.services.clients.open_meteo_client import (
    OpenMeteoClient,
    OpenMeteoClientError,
    _map_wmo_code_to_condition,
)


@pytest.fixture
def mock_normal_weather_response():
    """Mock 24-hour Open-Meteo response payload."""
    return {
        "latitude": 28.4177,
        "longitude": -81.5812,
        "generationtime_ms": 0.25,
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
            "temperature_2m": [74.5, 78.0, 82.3, 85.1, 87.0, 84.2],
            "precipitation_probability": [0, 10, 25, 60, 85, 100],
            "weather_code": [0, 1, 3, 61, 95, 65],
        },
    }


# ============================================================================
# SUCCESS & MAPPING TESTS
# ============================================================================


def test_get_hourly_forecast_success(mock_normal_weather_response):
    """Verify full mapping from Open-Meteo payload to list[WeatherHour]."""

    def custom_handler(request: httpx.Request) -> httpx.Response:
        assert "latitude=28.4177" in str(request.url)
        assert "longitude=-81.5812" in str(request.url)
        assert "temperature_unit=fahrenheit" in str(request.url)
        assert "timezone=America%2FNew_York" in str(
            request.url
        ) or "timezone=America/New_York" in str(request.url)
        return httpx.Response(200, json=mock_normal_weather_response)

    transport = httpx.MockTransport(custom_handler)
    client = httpx.Client(transport=transport)
    adapter = OpenMeteoClient(http_client=client)

    result = adapter.get_hourly_forecast(latitude=28.4177, longitude=-81.5812)

    assert len(result) == 6
    assert all(isinstance(h, WeatherHour) for h in result)

    # Check first item: 09:00, clear, 74.5°F, 0% prob
    first = result[0]
    assert first.timestamp == datetime(2026, 9, 17, 9, 0, tzinfo=PARK_TZ)
    assert first.temperature_f == 74.5
    assert first.precipitation_probability == 0.0
    assert first.condition == "clear"

    # Check 14:00 item: 100% prob, heavy rain (code 65)
    last = result[-1]
    assert last.timestamp == datetime(2026, 9, 17, 14, 0, tzinfo=PARK_TZ)
    assert last.temperature_f == 84.2
    assert last.precipitation_probability == 1.0
    assert last.condition == "rain"


def test_precipitation_probability_scaling(mock_normal_weather_response):
    """Verify that precipitation probability is strictly scaled to [0.0, 1.0]."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=mock_normal_weather_response)
    )
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    result = adapter.get_hourly_forecast()
    probabilities = [h.precipitation_probability for h in result]

    # [0, 10, 25, 60, 85, 100] -> [0.0, 0.1, 0.25, 0.6, 0.85, 1.0]
    assert probabilities == [0.0, 0.1, 0.25, 0.6, 0.85, 1.0]
    for p in probabilities:
        assert 0.0 <= p <= 1.0


def test_timezone_normalization(mock_normal_weather_response):
    """Verify all timestamps are normalized to America/New_York (PARK_TZ)."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=mock_normal_weather_response)
    )
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    result = adapter.get_hourly_forecast()

    for h in result:
        assert h.timestamp.tzinfo is not None
        assert str(h.timestamp.tzinfo) == "America/New_York"
        # Validate Pydantic base model awareness passes without validation error
        assert isinstance(h, WeatherHour)


def test_wmo_code_mapping():
    """Verify WMO weather codes map accurately to domain conditions."""
    assert _map_wmo_code_to_condition(0) == "clear"
    assert _map_wmo_code_to_condition(1) == "partly_cloudy"
    assert _map_wmo_code_to_condition(2) == "partly_cloudy"
    assert _map_wmo_code_to_condition(3) == "cloudy"
    assert _map_wmo_code_to_condition(45) == "fog"
    assert _map_wmo_code_to_condition(48) == "fog"
    assert _map_wmo_code_to_condition(51) == "drizzle"
    assert _map_wmo_code_to_condition(61) == "rain"
    assert _map_wmo_code_to_condition(63) == "rain"
    assert _map_wmo_code_to_condition(65) == "rain"
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
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    start = date(2026, 9, 18)
    end = date(2026, 9, 19)
    result = adapter.get_hourly_forecast(start_date=start, end_date=end)

    assert len(result) == 1
    assert captured_params.get("start_date") == "2026-09-18"
    assert captured_params.get("end_date") == "2026-09-19"


def test_get_weather_convenience_method(mock_normal_weather_response):
    """Verify get_weather method returns closest WeatherHour or matching hour."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=mock_normal_weather_response)
    )
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    target_time = datetime(2026, 9, 17, 11, 15, tzinfo=PARK_TZ)
    weather = adapter.get_weather(at_time=target_time)

    assert isinstance(weather, WeatherHour)
    # Closest hour is 11:00
    assert weather.timestamp == datetime(2026, 9, 17, 11, 0, tzinfo=PARK_TZ)
    assert weather.temperature_f == 82.3


# ============================================================================
# ERROR HANDLING & RECOVERABILITY TESTS
# ============================================================================


def test_http_500_error_raises_recoverable_exception():
    """Verify HTTP 500 error raises OpenMeteoClientError with status_code=500."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(500, text="Internal Server Error")
    )
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    with pytest.raises(OpenMeteoClientError) as exc_info:
        adapter.get_hourly_forecast()

    assert exc_info.value.status_code == 500
    assert "HTTP 500" in str(exc_info.value)


def test_http_503_error_raises_recoverable_exception():
    """Verify HTTP 503 error raises OpenMeteoClientError with status_code=503."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(503, text="Service Unavailable")
    )
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    with pytest.raises(OpenMeteoClientError) as exc_info:
        adapter.get_hourly_forecast()

    assert exc_info.value.status_code == 503


def test_timeout_raises_recoverable_exception():
    """Verify httpx.TimeoutException raises OpenMeteoClientError."""

    def timeout_handler(request: httpx.Request):
        raise httpx.ConnectTimeout("Connection timed out")

    transport = httpx.MockTransport(timeout_handler)
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    with pytest.raises(OpenMeteoClientError) as exc_info:
        adapter.get_hourly_forecast()

    assert "timed out" in str(exc_info.value).lower()
    assert isinstance(exc_info.value.original_error, httpx.TimeoutException)


def test_malformed_json_response_raises_recoverable_exception():
    """Verify non-JSON response raises OpenMeteoClientError."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text="<html>Error</html>")
    )
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    with pytest.raises(OpenMeteoClientError) as exc_info:
        adapter.get_hourly_forecast()

    assert (
        "malformed" in str(exc_info.value).lower()
        or "json" in str(exc_info.value).lower()
    )


def test_missing_hourly_field_raises_recoverable_exception():
    """Verify payload missing 'hourly' section raises OpenMeteoClientError."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200, json={"error": True, "reason": "invalid params"}
        )
    )
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    with pytest.raises(OpenMeteoClientError) as exc_info:
        adapter.get_hourly_forecast()

    assert "hourly" in str(exc_info.value).lower()


def test_mismatched_array_lengths_raises_recoverable_exception():
    """Verify payload with mismatched array lengths raises OpenMeteoClientError."""
    payload = {
        "hourly": {
            "time": ["2026-09-17T09:00", "2026-09-17T10:00"],
            "temperature_2m": [74.5],  # only 1 item
            "precipitation_probability": [0, 10],
            "weather_code": [0, 1],
        }
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    adapter = OpenMeteoClient(http_client=httpx.Client(transport=transport))

    with pytest.raises(OpenMeteoClientError) as exc_info:
        adapter.get_hourly_forecast()

    assert (
        "mismatched" in str(exc_info.value).lower()
        or "length" in str(exc_info.value).lower()
    )
