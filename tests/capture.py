"""The real payloads captured together on 2026-09-27, served through real clients.

``Provider`` wires ``ThemeParksClient`` and ``OpenMeteoClient`` to an
``httpx.MockTransport`` that serves tests/fixtures/snapshot_2026-09-27, counts
the calls, and can be told to fail -- no network in the suite.
"""

import json
from datetime import datetime
from pathlib import Path

import httpx

from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.open_meteo_client import OpenMeteoClient
from parkmind.services.clients.themeparks_client import ThemeParksClient

CAPTURE = Path(__file__).resolve().parent / "fixtures" / "snapshot_2026-09-27"
PARK_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"
NOW = datetime(2026, 9, 27, 11, 2, 30, tzinfo=PARK_TZ)  # inside the capture day, park open


def capture(name: str) -> dict:
    return json.loads((CAPTURE / name).read_text(encoding="utf-8"))


class Provider:
    """Serves the capture; counts calls; can be told to fail."""

    def __init__(self, *, parks_status: int = 200, schedule_status: int = 200, weather_status: int = 200):
        self.calls: list[str] = []
        self.parks_status, self.schedule_status, self.weather_status = parks_status, schedule_status, weather_status

    def parks(self) -> ThemeParksClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            if request.url.path.endswith("/live"):
                return httpx.Response(self.parks_status, json=capture("themeparks_live.json"))
            return httpx.Response(self.schedule_status, json=capture("themeparks_schedule.json"))

        return ThemeParksClient(
            PARK_ID, base_url="https://testserver", transport=httpx.MockTransport(handler), backoff_seconds=0
        )

    def weather(self) -> OpenMeteoClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            return httpx.Response(self.weather_status, json=capture("open_meteo_hourly.json"))

        return OpenMeteoClient(transport=httpx.MockTransport(handler), backoff_seconds=0)
