"""AttractionRepository -- catalog and operating windows (section 33 ``Attraction``, ``Park``).

Immutable park topology loaded from the park-data provider (``ParkDataPort``),
persisted so the planner and ParkGraph (P0-13) can work from a stored catalog.
"""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from parkmind.core.contracts import Attraction, Park


class AttractionRepository(Protocol):
    def save_catalog(self, park_id: str, attractions: Sequence[Attraction]) -> None:
        """Upsert the attractions of ``park_id`` (keyed by ``node_id``)."""
        ...

    def list_attractions(self, park_id: str) -> list[Attraction]:
        """The park's attractions ordered by ``node_id``."""
        ...

    def get_attraction(self, node_id: str) -> Attraction | None: ...

    def save_schedule(self, park: Park) -> None:
        """Upsert the operating window, keyed by ``park_id`` and the park-local
        date of ``opening_time``."""
        ...

    def get_schedule(self, park_id: str, on_date: date) -> Park | None: ...
