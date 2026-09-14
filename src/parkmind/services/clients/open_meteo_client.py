"""
Open-Meteo client — real weather.
API docs: https://open-meteo.com/en/docs
"""


class OpenMeteoClient:
    def get_weather(self, at_time) -> dict:
        raise NotImplementedError
