"""ParkGraph ? the planning space: attractions, shows, coordinates, opening
hours, walking edges.

Planning components use ParkGraph to query walking times without knowing
which concrete routing adapter (in-memory matrix, Haversine model, or external OSRM)
is providing the data.
"""

from typing import Any

from parkmind.services.ports import RoutingPort


class ParkGraph:
    """Planning topology and graph representation for the theme park."""

    def __init__(self, routing: RoutingPort | None = None) -> None:
        self._routing = routing

    def walk_minutes(self, a: str, b: str) -> float:
        """Estimate walking time in minutes between two park planning nodes."""
        if self._routing is None:
            raise RuntimeError("RoutingPort has not been configured in ParkGraph")
        return self._routing.walk_minutes(a, b)

    def open_at(self, attraction_id: str, t: Any) -> bool:
        raise NotImplementedError

    def showtimes(self, show_id: str) -> list[Any]:
        raise NotImplementedError
