"""Use case: fetch live weather + attraction context for planning.

Wraps the weather and theme-park clients so graph/ never imports
services.clients directly (see .importlinter boundary contract).
"""

from datetime import date

from parkmind.core.contracts import Attraction, WeatherHour
from parkmind.services.clients.open_meteo_client import (
    OpenMeteoClient,
    OpenMeteoClientError,
)
from parkmind.services.clients.themeparks_client import (
    ThemeParksClient,
    ThemeParksClientError,
)


class LoadLiveContextUseCase:
    def __init__(self, *, park_id: str, latitude: float, longitude: float):
        self._weather_client = OpenMeteoClient()
        self._parks_client = ThemeParksClient(park_id)
        self._latitude = latitude
        self._longitude = longitude

    def fetch_weather(self, *, start_date: date, end_date: date) -> list[WeatherHour]:
        try:
            return self._weather_client.get_hourly_forecast(
                latitude=self._latitude,
                longitude=self._longitude,
                start_date=start_date,
                end_date=end_date,
            )
        except OpenMeteoClientError as e:
            print(f"Warning: Could not fetch weather: {e}")
            return []

    def fetch_attractions(self) -> list[Attraction]:
        try:
            return self._parks_client.get_catalog()
        except ThemeParksClientError as e:
            print(f"Warning: Could not fetch attractions: {e}")
            return []
