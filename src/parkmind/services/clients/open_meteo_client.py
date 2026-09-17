"""
Open-Meteo client — real weather adapter.

Maps external hourly weather forecasts (temperature, precipitation probability,
WMO weather codes) to the internal WeatherHour domain model, normalizes timezones
to America/New_York (PARK_TZ), and handles API failures as recoverable data-quality
conditions.

API docs: https://open-meteo.com/en/docs
"""

from datetime import date, datetime
from typing import Any

import httpx

from parkmind.config.settings import settings
from parkmind.core.contracts import PARK_TZ, WeatherHour


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

    Fetches hourly forecast, normalizes timestamps to park timezone,
    and maps values to Pydantic WeatherHour models.
    """

    # Reference coordinates (Magic Kingdom / Walt Disney World, FL)
    DEFAULT_LATITUDE: float = 28.4177
    DEFAULT_LONGITUDE: float = -81.5812

    def __init__(
        self,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = (base_url or settings.OPEN_METEO_BASE_URL).rstrip("/")
        self._http_client = http_client
        self.timeout = timeout

    def _get_client(self) -> httpx.Client:
        if self._http_client is not None:
            return self._http_client
        return httpx.Client(timeout=self.timeout)

    def get_hourly_forecast(
        self,
        latitude: float = DEFAULT_LATITUDE,
        longitude: float = DEFAULT_LONGITUDE,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        forecast_days: int = 1,
    ) -> list[WeatherHour]:
        """
        Fetch hourly weather forecast for specified coordinates and time window.

        Args:
            latitude: Latitude of the theme park.
            longitude: Longitude of the theme park.
            start_date: Optional start date for forecast range.
            end_date: Optional end date for forecast range.
            forecast_days: Number of forecast days (default 1 if start/end not provided).

        Returns:
            list[WeatherHour]: Validated domain models with timezone-aware timestamps.

        Raises:
            OpenMeteoClientError: If network, HTTP status, or payload parsing fails.
        """
        params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
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

        endpoint = f"{self.base_url}/forecast"

        client = self._get_client()
        owns_client = self._http_client is None

        try:
            response = client.get(endpoint, params=params, timeout=self.timeout)
        except httpx.TimeoutException as e:
            raise OpenMeteoClientError(
                f"Open-Meteo API timed out after {self.timeout}s",
                original_error=e,
            ) from e
        except httpx.RequestError as e:
            raise OpenMeteoClientError(
                f"Open-Meteo network request failed: {e}",
                original_error=e,
            ) from e
        finally:
            if owns_client:
                client.close()

        if response.status_code != 200:
            raise OpenMeteoClientError(
                f"Open-Meteo API returned HTTP {response.status_code}: {response.text}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except Exception as e:
            raise OpenMeteoClientError(
                f"Malformed JSON response from Open-Meteo: {e}",
                status_code=response.status_code,
                original_error=e,
            ) from e

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

                # Open-Meteo returns precipitation_probability in percent (0..100)
                # WeatherHour contract requires float in [0.0, 1.0]
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
        at_time: datetime | None = None,
        latitude: float = DEFAULT_LATITUDE,
        longitude: float = DEFAULT_LONGITUDE,
    ) -> WeatherHour:
        """
        Fetch weather forecast for a specific timestamp (or closest matching hour).

        Args:
            at_time: Target datetime. If timezone-naive, PARK_TZ is assumed. Defaults to now.
            latitude: Theme park latitude.
            longitude: Theme park longitude.

        Returns:
            WeatherHour: Closest matching forecast hour.
        """
        if at_time is None:
            at_time = datetime.now(PARK_TZ)
        elif at_time.tzinfo is None:
            at_time = at_time.replace(tzinfo=PARK_TZ)
        else:
            at_time = at_time.astimezone(PARK_TZ)

        hourly = self.get_hourly_forecast(
            latitude=latitude,
            longitude=longitude,
            start_date=at_time.date(),
            end_date=at_time.date(),
        )

        if not hourly:
            # Fallback to general forecast if date-bounded query returned empty
            hourly = self.get_hourly_forecast(latitude=latitude, longitude=longitude)

        if not hourly:
            raise OpenMeteoClientError(
                "No weather forecast data returned for requested time"
            )

        # Find closest hour
        closest = min(
            hourly, key=lambda h: abs((h.timestamp - at_time).total_seconds())
        )
        return closest
