"""
Open-Meteo client — real weather adapter.

Maps external hourly weather forecasts (temperature, precipitation probability,
WMO weather codes) to the internal WeatherHour domain model, normalizes timezones
to America/New_York (PARK_TZ), implements bounded retries with linear backoff,
and handles API failures as recoverable data-quality conditions.

API docs: https://open-meteo.com/en/docs
"""

import time
from datetime import date, datetime
from typing import Any, Self

import httpx

from parkmind.config.settings import settings
from parkmind.core.contracts import PARK_TZ, WeatherHour

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class OpenMeteoClientError(Exception):
    """
    Recoverable error raised when Open-Meteo API fails or returns invalid data.

    Allows upstream callers (e.g., LiveContext loader, MonitorEventsUseCase)
    to gracefully degrade coverage instead of crashing.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.original_error = original_error


def _map_wmo_code_to_condition(code: int) -> str:
    """
    Map WMO Weather interpretation codes (WW) to ParkMind condition taxonomy.

    Reference: https://open-meteo.com/en/docs
    """
    match code:
        case 0:
            return "clear"
        case 1 | 2:
            return "partly_cloudy"
        case 3:
            return "cloudy"
        case 45 | 48:
            return "fog"
        case 51 | 53 | 55 | 56 | 57:
            return "drizzle"
        case 61 | 63 | 65 | 66 | 67:
            return "rain"
        case 71 | 73 | 75 | 77:
            return "snow"
        case 80 | 81 | 82:
            return "rain"
        case 85 | 86:
            return "snow"
        case 95 | 96 | 99:
            return "storm"
        case _:
            return "unknown"


class OpenMeteoClient:
    """
    HTTP Client adapter for Open-Meteo Weather API.

    Implements WeatherPort structural protocol.
    Fetches hourly forecast, normalizes timestamps to park timezone,
    and maps values to Pydantic WeatherHour models.
    """

    # Reference coordinates (Magic Kingdom / Walt Disney World, FL)
    DEFAULT_LATITUDE: float = 28.4177
    DEFAULT_LONGITUDE: float = -81.5812

    def __init__(
        self,
        *,
        base_url: str = settings.OPEN_METEO_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        default_latitude: float = DEFAULT_LATITUDE,
        default_longitude: float = DEFAULT_LONGITUDE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_latitude = default_latitude
        self.default_longitude = default_longitude
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._client = http_client or httpx.Client(
            base_url=self.base_url,
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        """Close the underlying HTTP client session."""
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute an HTTP request with bounded retry and backoff on transient failures.
        """
        for attempt in range(1, self.max_retries + 1):
            is_last_attempt = attempt == self.max_retries

            try:
                response = self._client.get(endpoint, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if is_last_attempt:
                    raise OpenMeteoClientError(
                        f"Open-Meteo request failed after {self.max_retries} attempts: {exc}",
                        original_error=exc,
                    ) from exc
                time.sleep(self.backoff_seconds * attempt)
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES:
                if is_last_attempt:
                    raise OpenMeteoClientError(
                        f"Open-Meteo returned HTTP {response.status_code} after {self.max_retries} attempts",
                        status_code=response.status_code,
                    )
                time.sleep(self.backoff_seconds * attempt)
                continue

            if response.status_code >= 400:
                # Open-Meteo 400 format: {"error": true, "reason": "..."}
                try:
                    err_payload = response.json()
                    reason = (
                        err_payload.get("reason", response.text)
                        if isinstance(err_payload, dict)
                        else response.text
                    )
                except (ValueError, KeyError, TypeError):
                    reason = response.text

                raise OpenMeteoClientError(
                    f"Open-Meteo API returned HTTP {response.status_code}: {reason}",
                    status_code=response.status_code,
                )

            try:
                return response.json()
            except ValueError as exc:
                raise OpenMeteoClientError(
                    f"Malformed non-JSON response from Open-Meteo: {exc}",
                    status_code=response.status_code,
                    original_error=exc,
                ) from exc

        raise AssertionError("unreachable")

    def get_hourly_forecast(
        self,
        latitude: float | None = None,
        longitude: float | None = None,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        forecast_days: int = 1,
    ) -> list[WeatherHour]:
        """
        Fetch hourly weather forecast for specified coordinates and time window.

        Args:
            latitude: Latitude of the theme park (defaults to default_latitude).
            longitude: Longitude of the theme park (defaults to default_longitude).
            start_date: Optional start date for forecast range.
            end_date: Optional end date for forecast range.
            forecast_days: Number of forecast days (default 1 if start/end not provided).

        Returns:
            list[WeatherHour]: Validated domain models with timezone-aware timestamps.

        Raises:
            OpenMeteoClientError: If network, HTTP status, or payload parsing fails.
        """
        lat = latitude if latitude is not None else self.default_latitude
        lon = longitude if longitude is not None else self.default_longitude

        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "temperature_unit": "fahrenheit",
            "timezone": "America/New_York",
        }

        if start_date:
            params["start_date"] = (
                start_date.isoformat()
                if isinstance(start_date, (date, datetime))
                else str(start_date)
            )
        if end_date:
            params["end_date"] = (
                end_date.isoformat()
                if isinstance(end_date, (date, datetime))
                else str(end_date)
            )
        if not start_date and not end_date:
            params["forecast_days"] = forecast_days

        payload = self._request("/forecast", params=params)

        if not isinstance(payload, dict) or "hourly" not in payload:
            raise OpenMeteoClientError(
                "Malformed response: 'hourly' section missing from Open-Meteo payload"
            )

        hourly = payload["hourly"]
        if not isinstance(hourly, dict):
            raise OpenMeteoClientError(
                "Malformed response: 'hourly' section must be a dictionary"
            )

        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        probs = hourly.get("precipitation_probability", [])
        codes = hourly.get("weather_code", [])

        if not (len(times) == len(temps) == len(probs) == len(codes)):
            raise OpenMeteoClientError(
                f"Mismatched array lengths in hourly weather response: "
                f"time={len(times)}, temp={len(temps)}, prob={len(probs)}, code={len(codes)}"
            )

        result: list[WeatherHour] = []
        for i in range(len(times)):
            try:
                dt = datetime.fromisoformat(times[i])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=PARK_TZ)
                else:
                    dt = dt.astimezone(PARK_TZ)

                prob_percent = float(probs[i])
                prob_normalized = max(0.0, min(1.0, prob_percent / 100.0))

                condition = _map_wmo_code_to_condition(int(codes[i]))
                temp_f = float(temps[i])

                result.append(
                    WeatherHour(
                        timestamp=dt,
                        condition=condition,
                        temperature_f=temp_f,
                        precipitation_probability=prob_normalized,
                    )
                )
            except Exception as e:
                raise OpenMeteoClientError(
                    f"Failed to parse hourly weather item at index {i}: {e}",
                    original_error=e,
                ) from e

        return result

    def get_weather(
        self,
        at_time: datetime,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> WeatherHour:
        """
        Fetch weather forecast for a specific timestamp (or closest matching hour).

        Args:
            at_time: Explicit target datetime. If timezone-naive, PARK_TZ is assumed.
            latitude: Theme park latitude (defaults to default_latitude).
            longitude: Theme park longitude (defaults to default_longitude).

        Returns:
            WeatherHour: Closest matching forecast hour on the target date.

        Raises:
            OpenMeteoClientError: If no data is returned for the requested date.
        """
        if at_time.tzinfo is None:
            normalized_time = at_time.replace(tzinfo=PARK_TZ)
        else:
            normalized_time = at_time.astimezone(PARK_TZ)

        lat = latitude if latitude is not None else self.default_latitude
        lon = longitude if longitude is not None else self.default_longitude

        hourly = self.get_hourly_forecast(
            latitude=lat,
            longitude=lon,
            start_date=normalized_time.date(),
            end_date=normalized_time.date(),
        )

        if not hourly:
            raise OpenMeteoClientError(
                f"No hourly weather forecast data available for date {normalized_time.date().isoformat()}"
            )

        closest = min(
            hourly, key=lambda h: abs((h.timestamp - normalized_time).total_seconds())
        )
        return closest
