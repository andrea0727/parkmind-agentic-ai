"""RoutingClient adapter ? implements RoutingPort.

Provides walking time estimation between planning nodes with:
1. Exact zero-duration for same-node queries.
2. Precomputed matrix lookups for direct paths.
3. Coordinate-based Haversine calculations with pedestrian detour (tortuosity) factors.
4. Robust and configurable fallback behavior when nodes or routes are missing.
"""

import logging
import math
from typing import Any

from .routing_reference_data import (
    MAGIC_KINGDOM_DIRECT_WALKING_TIMES,
    MAGIC_KINGDOM_NODE_COORDINATES,
)

logger = logging.getLogger(__name__)

# Default constants
DEFAULT_WALKING_SPEED_METERS_PER_MINUTE: float = 75.0  # ~4.5 km/h / 2.8 mph
DEFAULT_FALLBACK_WALKING_MINUTES: float = 10.0
DEFAULT_TORTUOSITY_FACTOR: float = (
    1.2  # Real pedestrian paths vs straight-line distance
)
EARTH_RADIUS_METERS: float = 6371000.0


class RoutingClientError(Exception):
    """Base exception for routing client errors."""


class InvalidRouteError(RoutingClientError, ValueError):
    """Raised when origin/destination identifiers or parameters are invalid."""


class RouteNotFoundError(RoutingClientError):
    """Raised when a route between nodes cannot be resolved and fallback is disabled."""


class RoutingSchemaError(RoutingClientError):
    """Raised when routing response payloads are malformed."""


class RoutingUnavailableError(RoutingClientError):
    """Raised when an external routing provider service is unavailable."""


def calculate_haversine_distance_meters(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Calculate great-circle distance between two geographic coordinates in meters."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METERS * c


class RoutingClient:
    """
    Adapter implementing RoutingPort.

    Estimates walking minutes between nodes using a multi-tiered resolution:
    - Same node: 0.0 minutes
    - Direct matrix entry: explicit curated value
    - Coordinate match: Haversine distance with tortuosity adjustment
    - Fallback: explicit default duration or typed error depending on configuration
    """

    def __init__(
        self,
        walking_speed_meters_per_minute: float = DEFAULT_WALKING_SPEED_METERS_PER_MINUTE,
        default_fallback_minutes: float = DEFAULT_FALLBACK_WALKING_MINUTES,
        tortuosity_factor: float = DEFAULT_TORTUOSITY_FACTOR,
        fallback_enabled: bool = True,
        custom_matrix: dict[tuple[str, str], float] | None = None,
        custom_coordinates: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        if walking_speed_meters_per_minute <= 0:
            raise ValueError(
                "walking_speed_meters_per_minute must be strictly positive"
            )
        if default_fallback_minutes < 0:
            raise ValueError("default_fallback_minutes must be non-negative")
        if tortuosity_factor < 1.0:
            raise ValueError("tortuosity_factor must be >= 1.0")

        self.walking_speed_meters_per_minute = float(walking_speed_meters_per_minute)
        self.default_fallback_minutes = float(default_fallback_minutes)
        self.tortuosity_factor = float(tortuosity_factor)
        self.fallback_enabled = bool(fallback_enabled)

        # Merge defaults with custom definitions
        self.matrix: dict[tuple[str, str], float] = dict(
            MAGIC_KINGDOM_DIRECT_WALKING_TIMES
        )
        if custom_matrix:
            self.matrix.update(custom_matrix)

        self.coordinates: dict[str, tuple[float, float]] = dict(
            MAGIC_KINGDOM_NODE_COORDINATES
        )
        if custom_coordinates:
            self.coordinates.update(custom_coordinates)

    def walk_minutes(self, origin_node_id: str, destination_node_id: str) -> float:
        """
        Estimate walking duration in minutes between two planning nodes.

        Args:
            origin_node_id: Unique identifier for starting node.
            destination_node_id: Unique identifier for destination node.

        Returns:
            float: Estimated walking time in minutes (>= 0.0).

        Raises:
            InvalidRouteError: If node IDs are not valid non-empty strings.
            RouteNotFoundError: If route cannot be resolved and fallback_enabled=False.
        """
        self._validate_node_id(origin_node_id, "origin_node_id")
        self._validate_node_id(destination_node_id, "destination_node_id")

        orig = origin_node_id.strip()
        dest = destination_node_id.strip()

        # Rule 1: Identity
        if orig == dest:
            return 0.0

        # Rule 2: Explicit matrix lookup (both directions)
        if (orig, dest) in self.matrix:
            return max(0.0, float(self.matrix[(orig, dest)]))
        if (dest, orig) in self.matrix:
            return max(0.0, float(self.matrix[(dest, orig)]))

        # Rule 3: Coordinate-based estimation
        if orig in self.coordinates and dest in self.coordinates:
            lat1, lon1 = self.coordinates[orig]
            lat2, lon2 = self.coordinates[dest]
            distance_meters = calculate_haversine_distance_meters(
                lat1, lon1, lat2, lon2
            )
            detour_distance = distance_meters * self.tortuosity_factor
            minutes = detour_distance / self.walking_speed_meters_per_minute
            # Minimum walking threshold for distinct physical nodes: 0.5 minutes
            return round(max(0.5, minutes), 1)

        # Rule 4: Explicit Fallback
        if self.fallback_enabled:
            logger.warning(
                "Explicit routing fallback applied: route between '%s' and '%s' "
                "missing from topology. Using fallback %.1f minutes.",
                orig,
                dest,
                self.default_fallback_minutes,
            )
            return float(self.default_fallback_minutes)

        raise RouteNotFoundError(
            f"No route or coordinates found between node '{orig}' and '{dest}', "
            "and routing fallback is disabled."
        )

    def _validate_node_id(self, node_id: Any, param_name: str) -> None:
        """Validate that a node ID parameter is a non-empty string."""
        if not isinstance(node_id, str) or not node_id.strip():
            raise InvalidRouteError(
                f"Parameter '{param_name}' must be a non-empty string. Given: {node_id!r}"
            )
