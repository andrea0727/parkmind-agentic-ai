"""ParkDataPort — structural contract for park-data adapters.

ThemeParksClient (services/clients/themeparks_client.py) implements this
today. Every method returns ParkMind's internal typed contracts only —
never raw provider JSON/dicts. See P0-07 Done-when: "Raw provider schema
does not leak into core."
"""

from datetime import date, datetime
from typing import Protocol

from parkmind.core.contracts import Attraction, AttractionStatus, Park, WaitEstimate


class ParkDataPort(Protocol):
    def get_catalog(self) -> list[Attraction]: ...

    def get_schedule(self, on_date: date) -> Park: ...

    def get_live_waits(self, attraction_ids: list[str]) -> dict[str, WaitEstimate]: ...

    def get_attraction_status(
        self, attraction_ids: list[str]
    ) -> dict[str, AttractionStatus]: ...

    def get_showtimes(self, attraction_ids: list[str]) -> dict[str, list[datetime]]: ...
