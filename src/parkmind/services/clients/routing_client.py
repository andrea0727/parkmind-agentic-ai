"""RoutingClient adapter — implements RoutingPort and WalkEstimator.

Provides walking time estimation between planning nodes with:
1. Exact zero-duration for same-node queries.
2. Precomputed matrix lookups for direct paths (if custom matrix provided).
3. Coordinate-based Haversine calculations with pedestrian detour (tortuosity) factors.
4. Strict mode / fail-closed by default, with optional explicit fallback behavior.
"""

import logging
import math
from typing import Any

from parkmind.services.ports import (
    InvalidRouteError,
    RouteNotFoundError,
    RoutingError,
    WalkEstimate,
)

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

# Backward compatibility alias
RoutingClientError = RoutingError


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
    - Direct matrix entry: explicit override value (if provided)
    - Coordinate match: Haversine distance with tortuosity adjustment
    - Strict fail-closed by default (RouteNotFoundError/RoutingNotFoundError)
      or explicit fallback when configured with fallback_enabled=True.
    """

    def __init__(
        self,
        walking_speed_meters_per_minute: float = DEFAULT_WALKING_SPEED_METERS_PER_MINUTE,
        default_fallback_minutes: float = DEFAULT_FALLBACK_WALKING_MINUTES,
        tortuosity_factor: float = DEFAULT_TORTUOSITY_FACTOR,
        fallback_enabled: bool = False,
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

        resolved = self._resolve(orig, dest)
        if resolved is not None and resolved.minutes is not None:
            return resolved.minutes

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

    def estimate_walk(
        self, origin_node_id: str, destination_node_id: str
    ) -> WalkEstimate:
        """``WalkEstimator``: the same rules 1-3 with their basis; never the fallback.

        Without a matrix entry or coordinates the answer is ``unknown`` with no
        minutes, even when ``fallback_enabled`` -- a published walking time must
        not be a silent default (P0-25).
        """
        self._validate_node_id(origin_node_id, "origin_node_id")
        self._validate_node_id(destination_node_id, "destination_node_id")
        resolved = self._resolve(origin_node_id.strip(), destination_node_id.strip())
        return resolved if resolved is not None else WalkEstimate(None, "unknown")

    def _resolve(self, orig: str, dest: str) -> WalkEstimate | None:
        """Rules 1-3 (identity, matrix, coordinates); ``None`` when none applies."""
        # Rule 1: Identity
        if orig == dest:
            return WalkEstimate(0.0, "identity")

        # Rule 2: Explicit matrix lookup (both directions)
        if (orig, dest) in self.matrix:
            return WalkEstimate(max(0.0, float(self.matrix[(orig, dest)])), "curated")
        if (dest, orig) in self.matrix:
            return WalkEstimate(max(0.0, float(self.matrix[(dest, orig)])), "curated")

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
            return WalkEstimate(round(max(0.5, minutes), 1), "estimated")

        return None

    def _validate_node_id(self, node_id: Any, param_name: str) -> None:
        """Validate that a node ID parameter is a non-empty string."""
        if not isinstance(node_id, str) or not node_id.strip():
            raise InvalidRouteError(
                f"Parameter '{param_name}' must be a non-empty string. Given: {node_id!r}"
            )
