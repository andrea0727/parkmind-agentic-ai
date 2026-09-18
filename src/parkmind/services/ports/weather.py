"""WeatherPort — structural contract for weather adapters.

OpenMeteoClient (services/clients/open_meteo_client.py) implements this
today. Every method returns ParkMind's internal typed contracts only —
never raw provider JSON/dicts.
"""

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from parkmind.core.contracts import WeatherHour


@runtime_checkable
class WeatherPort(Protocol):
    def get_hourly_forecast(
        self,
        latitude: float,
        longitude: float,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        forecast_days: int = 1,
    ) -> list[WeatherHour]: ...

    def get_weather(
        self,
        at_time: datetime,
        latitude: float,
        longitude: float,
    ) -> WeatherHour: ...
