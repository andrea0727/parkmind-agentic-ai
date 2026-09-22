"""WeatherPort — structural contract for weather adapters.

OpenMeteoClient (services/clients/open_meteo_client.py) implements this
today. Every method returns ParkMind's internal typed contracts only —
never raw provider JSON/dicts.
"""

from datetime import date, datetime
from typing import Protocol

from parkmind.core.contracts import WeatherHour


class WeatherPort(Protocol):
    def get_hourly_forecast(
        self,
        latitude: float | None = None,
        longitude: float | None = None,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        forecast_days: int = 1,
    ) -> list[WeatherHour]: ...

    def get_weather(
        self,
        at_time: datetime,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> WeatherHour: ...
