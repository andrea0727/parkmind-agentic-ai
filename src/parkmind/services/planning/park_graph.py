"""ParkGraph — the planning space: attractions, shows, coordinates, opening
hours, walking edges.

Planning components use ParkGraph to query walking times without knowing
which concrete routing adapter (in-memory matrix, Haversine model, or external OSRM)
is providing the data.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from parkmind.core.contracts import Attraction, Park
from parkmind.services.planning.park_graph_reference_data import (
    MAGIC_KINGDOM_LOCATION_ALIASES,
)
from parkmind.services.ports import RoutingPort

_LOCATION_QUERY_PREFIXES = ("near ", "at ", "in ", "by ")


def _normalize_location_query(query: str) -> str:
    normalized = query.strip().lower()
    for prefix in _LOCATION_QUERY_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized


class ParkGraph:
    """Planning topology and graph representation for the theme park."""

    def __init__(
        self,
        routing: RoutingPort | None = None,
        *,
        attractions: Sequence[Attraction] = (),
        park: Park | None = None,
        showtimes: Mapping[str, Sequence[datetime]] | None = None,
        location_aliases: Mapping[str, str] | None = None,
    ) -> None:
        self._routing = routing
        self._attractions = {a.node_id: a for a in attractions}
        self._park = park
        self._showtimes = dict(showtimes or {})
        self._location_aliases = dict(
            location_aliases
            if location_aliases is not None
            else MAGIC_KINGDOM_LOCATION_ALIASES
        )

    def walk_minutes(self, a: str, b: str) -> float:
        """Estimate walking time in minutes between two park planning nodes."""
        if self._routing is None:
            raise RuntimeError("RoutingPort has not been configured in ParkGraph")
        return self._routing.walk_minutes(a, b)

    def open_at(self, attraction_id: str, t: datetime) -> bool:
        """Whether ``t`` falls within the park's operating window.

        Checks the shared park-wide schedule only: no per-attraction static
        schedule exists in the domain model today (per-attraction operating
        state is ``AttractionStatus``, a live concern fetched via
        ``ParkDataPort.get_attraction_status``, not a static topology fact).
        ``attraction_id`` is accepted for interface symmetry with the rest of
        ParkGraph's node-keyed queries.
        """
        if self._park is None:
            raise RuntimeError("Park schedule has not been configured in ParkGraph")
        return self._park.opening_time <= t <= self._park.closing_time

    def showtimes(self, show_id: str) -> list[datetime]:
        """Known showtimes for ``show_id``, or an empty list if none are known."""
        return list(self._showtimes.get(show_id, []))

    def resolve_location(self, query: str) -> str | None:
        """Resolve a natural-language location query (e.g. "near Frontierland")
        to a node id. Returns ``None`` -- an explicit miss -- for unknown aliases.
        """
        return self._location_aliases.get(_normalize_location_query(query))

    def filter_eligible(
        self,
        node_ids: Sequence[str],
        *,
        guest_height_cm: float | None = None,
        outdoor_ok: bool = True,
    ) -> list[str]:
        """Filter ``node_ids`` by known eligibility metadata (height, outdoor).

        A node id with no known ``Attraction`` metadata (e.g. a land or meal/
        rest node) passes through unfiltered: there is nothing to disqualify
        it on.
        """
        eligible = []
        for node_id in node_ids:
            attraction = self._attractions.get(node_id)
            if attraction is None:
                eligible.append(node_id)
                continue
            if (
                guest_height_cm is not None
                and attraction.height_restriction_cm is not None
                and guest_height_cm < attraction.height_restriction_cm
            ):
                continue
            if not outdoor_ok and attraction.outdoor:
                continue
            eligible.append(node_id)
        return eligible
