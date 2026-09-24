"""
Open-Meteo client — real weather adapter.
API docs: https://open-meteo.com/en/docs

Implements services.ports.weather.WeatherPort structurally (duck-typed,
no inheritance). Public methods never return raw provider JSON/dicts —
maps provider forecast data to WeatherHour domain models in PARK_TZ.

Retry/error policy is adapter-level only (reference pattern for services/clients).
The retry loop and backoff formula themselves live in the shared
`services/clients/_retry.py` helper (issue #56); this module owns only the
policy values and what each outcome means.

- Attempts: `max_retries` is the TOTAL number of attempts (>= 1, validated).
  Backoff is exponential, `backoff_seconds * 2 ** (attempt - 1)`, slept only
  BETWEEN attempts: with the defaults (3 attempts, 0.5s) the delays are 0.5s
  then 1.0s.
- Retried: timeouts, transport errors, and HTTP 429/500/502/503/504. Exhausted
  retries raise OpenMeteoUnavailableError.
- Never retried: 404 (OpenMeteoNotFoundError) and any other 4xx
  (OpenMeteoClientError).
- Redirects (3xx) are NOT followed and NOT retried: they raise
  OpenMeteoSchemaError. A redirect means the provider's URL contract changed,
  so we fail closed instead of silently following it. Retrying is pointless too:
  the same URL returns the same redirect.
- Malformed payloads, missing hourly fields, mismatched array lengths and
  non-JSON bodies also raise OpenMeteoSchemaError; malformed data is never
  silently swallowed or coerced into a best guess.
"""

import logging
from datetime import date, datetime
from typing import Any, Self

import httpx

from parkmind.config.settings import settings
from parkmind.core.contracts import PARK_TZ, WeatherHour

from ._retry import (
    RETRYABLE_STATUS_CODES,
    RetriesExhausted,
    RetryPolicy,
    send_with_retry,
)

logger = logging.getLogger(__name__)


class OpenMeteoClientError(Exception):
    """Base class for all expected OpenMeteoClient failures."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.original_error = original_error


class OpenMeteoNotFoundError(OpenMeteoClientError):
    """Provider returned 404, or no forecast data was found for the requested date."""


class OpenMeteoUnavailableError(OpenMeteoClientError):
    """Transient failure (timeout/connection/5xx/429) survived all retries."""


class OpenMeteoSchemaError(OpenMeteoClientError):
    """Provider response didn't match the expected shape — redirect (3xx),
    missing field, mismatched array lengths, non-JSON body, ...
    Contract drift, never silently swallowed or coerced."""


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
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        default_latitude: float = DEFAULT_LATITUDE,
        default_longitude: float = DEFAULT_LONGITUDE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_latitude = default_latitude
        self.default_longitude = default_longitude
        self._retry_policy = RetryPolicy(max_attempts=max_retries, backoff_seconds=backoff_seconds)
        self._client = httpx.Client(
            base_url=self.base_url,
            transport=transport,
            timeout=timeout,
        )

    @property
    def max_retries(self) -> int:
        return self._retry_policy.max_attempts

    @property
    def backoff_seconds(self) -> float:
        return self._retry_policy.backoff_seconds

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
        try:
            response = send_with_retry(
                lambda: self._client.get(endpoint, params=params),
                policy=self._retry_policy,
            )
        except RetriesExhausted as exc:
            raise OpenMeteoUnavailableError(
                f"Open-Meteo request failed after {exc.attempts} attempts: {exc.last_error}",
                original_error=exc.last_error,
            ) from exc

        if response.status_code == 404:
            raise OpenMeteoNotFoundError(
                f"{endpoint} returned 404",
                status_code=404,
            )

        if response.status_code in RETRYABLE_STATUS_CODES:
            raise OpenMeteoUnavailableError(
                f"Open-Meteo returned HTTP {response.status_code} after "
                f"{self._retry_policy.max_attempts} attempts",
                status_code=response.status_code,
            )

        if 300 <= response.status_code < 400:
            # Policy: fail closed. httpx.Client() defaults to
            # follow_redirects=False, and we keep it: a 3xx means the
            # provider's URL contract changed. Not retried either.
            raise OpenMeteoSchemaError(
                f"{endpoint} returned unhandled redirect (HTTP {response.status_code})",
                status_code=response.status_code,
            )

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
            raise OpenMeteoSchemaError(
                f"Malformed non-JSON response from Open-Meteo: {exc}",
                status_code=response.status_code,
                original_error=exc,
            ) from exc

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

        # Open-Meteo returns 400 Bad Request if start_date is supplied without end_date (or vice versa)
        if start_date and not end_date:
            end_date = start_date
        elif end_date and not start_date:
            start_date = end_date

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
            raise OpenMeteoSchemaError(
                "Malformed response: 'hourly' section missing from Open-Meteo payload"
            )

        hourly = payload["hourly"]
        if not isinstance(hourly, dict):
            raise OpenMeteoSchemaError(
                "Malformed response: 'hourly' section must be a dictionary"
            )

        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        probs = hourly.get("precipitation_probability", [])
        codes = hourly.get("weather_code", [])

        if not (
            isinstance(times, list)
            and isinstance(temps, list)
            and isinstance(probs, list)
            and isinstance(codes, list)
        ):
            raise OpenMeteoSchemaError(
                "Malformed response: hourly data fields must be lists"
            )

        if not (len(times) == len(temps) == len(probs) == len(codes)):
            raise OpenMeteoSchemaError(
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
                raise OpenMeteoSchemaError(
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
            OpenMeteoNotFoundError: If no data is returned for the requested date.
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
            raise OpenMeteoNotFoundError(
                f"No hourly weather forecast data available for date {normalized_time.date().isoformat()}"
            )

        closest = min(
            hourly, key=lambda h: abs((h.timestamp - normalized_time).total_seconds())
        )
        return closest
