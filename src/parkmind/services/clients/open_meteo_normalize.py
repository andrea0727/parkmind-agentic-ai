"""Open-Meteo raw payload -> ``WeatherHour`` contracts, with no HTTP (backlog P0-11).

Pure function over the provider's JSON, used by ``OpenMeteoClient`` after it
fetches a payload, and usable on a stored raw payload so a snapshot can be
re-normalized instead of re-collected (Architecture section 41, C21).

The client asks Open-Meteo for ``timezone=America/New_York`` and Fahrenheit, so
hourly ``time`` values arrive as naive park-local times. A stored payload is
only read that way if it says so: a declared ``timezone`` other than the park's,
or a temperature unit other than Fahrenheit, is contract drift and raises.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from parkmind.core.contracts import PARK_TZ, WeatherHour

from .open_meteo_errors import OpenMeteoSchemaError

FAHRENHEIT_UNITS = frozenset({"°F", "F"})


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


def check_payload_units(payload: Mapping[str, Any]) -> None:
    """Fail closed when a payload declares a timezone or temperature unit we don't read."""
    declared_tz = payload.get("timezone")
    if declared_tz is not None and declared_tz != PARK_TZ.key:
        raise OpenMeteoSchemaError(
            f"payload timezone {declared_tz!r} is not the park timezone {PARK_TZ.key!r}"
        )
    units = payload.get("hourly_units")
    if isinstance(units, Mapping):
        unit = units.get("temperature_2m")
        if unit is not None and unit not in FAHRENHEIT_UNITS:
            raise OpenMeteoSchemaError(f"temperature unit {unit!r} is not Fahrenheit")


def parse_hourly_forecast(payload: Any) -> list[WeatherHour]:
    """An Open-Meteo ``/forecast`` hourly payload as park-local ``WeatherHour``s."""
    if not isinstance(payload, dict) or "hourly" not in payload:
        raise OpenMeteoSchemaError(
            "Malformed response: 'hourly' section missing from Open-Meteo payload"
        )

    check_payload_units(payload)

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
