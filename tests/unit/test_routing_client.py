"""Unit tests for RoutingClient adapter and RoutingPort."""

import logging

import pytest

from parkmind.services.clients.routing_client import (
    DEFAULT_FALLBACK_WALKING_MINUTES,
    DEFAULT_WALKING_SPEED_METERS_PER_MINUTE,
    InvalidRouteError,
    RouteNotFoundError,
    RoutingClient,
    calculate_haversine_distance_meters,
)
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import RoutingPort


def _accepts_port(port: RoutingPort) -> None:
    """mypy-only check that RoutingClient satisfies RoutingPort structurally."""


HUB_ID = "90d79335-c907-4069-a021-d0fe1ec73ae2"
SPACE_MTN_ID = "b2260923-9315-40fd-9c6b-44dd811dbe64"
TRON_ID = "5a43d1a7-ad53-4d25-abfe-25625f0da304"
PIRATES_ID = "352feb94-e52e-45eb-9c92-e4b44c6b1a9d"
BIG_THUNDER_ID = "de3309ca-97d5-4211-bffe-739fed47e92f"
TIANA_ID = "73cb9445-0695-47a3-87ce-d08ae36b5f3c"


# ============================================================================
# PROTOCOL & INITIALIZATION TESTS
# ============================================================================


def test_routing_client_satisfies_protocol():
    """Verify RoutingClient satisfies RoutingPort interface."""
    client = RoutingClient()
    _accepts_port(client)
    assert isinstance(client, RoutingPort)


def test_default_initialization():
    """Verify default parameters."""
    client = RoutingClient()
    assert (
        client.walking_speed_meters_per_minute
        == DEFAULT_WALKING_SPEED_METERS_PER_MINUTE
    )
    assert client.default_fallback_minutes == DEFAULT_FALLBACK_WALKING_MINUTES
    assert client.tortuosity_factor == 1.2
    assert client.fallback_enabled is True


def test_invalid_initialization_parameters():
    """Verify constructor raises ValueError on negative or invalid configuration."""
    with pytest.raises(
        ValueError, match="walking_speed_meters_per_minute must be strictly positive"
    ):
        RoutingClient(walking_speed_meters_per_minute=0)

    with pytest.raises(
        ValueError, match="walking_speed_meters_per_minute must be strictly positive"
    ):
        RoutingClient(walking_speed_meters_per_minute=-10.0)

    with pytest.raises(
        ValueError, match="default_fallback_minutes must be non-negative"
    ):
        RoutingClient(default_fallback_minutes=-1.0)

    with pytest.raises(ValueError, match="tortuosity_factor must be >= 1.0"):
        RoutingClient(tortuosity_factor=0.8)


# ============================================================================
# NORMAL ROUTE ESTIMATION TESTS
# ============================================================================


def test_same_node_returns_zero():
    """Identity check: walking from a node to itself is always 0.0 minutes."""
    client = RoutingClient()
    assert client.walk_minutes(SPACE_MTN_ID, SPACE_MTN_ID) == 0.0
    assert client.walk_minutes("any-node-id", "any-node-id") == 0.0


def test_direct_matrix_lookup():
    """Known pair in precalculated matrix returns exact minutes."""
    client = RoutingClient()
    # Space Mtn <-> TRON is 2.5 minutes in direct table
    assert client.walk_minutes(SPACE_MTN_ID, TRON_ID) == 2.5
    # Hub <-> Big Thunder is 6.0 minutes
    assert client.walk_minutes(HUB_ID, BIG_THUNDER_ID) == 6.0


def test_matrix_bidirectional_lookup():
    """Lookups in reverse direction should work symmetrically."""
    client = RoutingClient()
    forward = client.walk_minutes(HUB_ID, PIRATES_ID)
    backward = client.walk_minutes(PIRATES_ID, HUB_ID)
    assert forward == backward == 4.5


def test_custom_matrix_override():
    """Custom matrix takes precedence over defaults."""
    custom = {(SPACE_MTN_ID, TRON_ID): 1.0}
    client = RoutingClient(custom_matrix=custom)
    assert client.walk_minutes(SPACE_MTN_ID, TRON_ID) == 1.0


# ============================================================================
# COORDINATE / HAVERSINE TESTS
# ============================================================================


def test_haversine_distance_calculation():
    """Verify distance between two known geographic points."""
    # Distance between Magic Kingdom Hub (28.4194, -81.5812) and Space Mtn (28.4192, -81.5772)
    dist = calculate_haversine_distance_meters(28.4194, -81.5812, 28.4192, -81.5772)
    assert 380.0 < dist < 410.0  # Approx 391 meters


def test_coordinate_based_estimation_without_matrix_entry():
    """Nodes with coordinates but no explicit matrix edge use Haversine estimation."""
    custom_coords = {
        "node_a": (28.4190, -81.5810),
        "node_b": (28.4200, -81.5810),  # approx 111 meters north
    }
    client = RoutingClient(
        custom_coordinates=custom_coords,
        walking_speed_meters_per_minute=75.0,
        tortuosity_factor=1.2,
    )
    # 111m * 1.2 = ~133.2m -> / 75 m/min = ~1.8 min
    minutes = client.walk_minutes("node_a", "node_b")
    assert 1.5 <= minutes <= 2.2


# ============================================================================
# FALLBACK & MISSING ROUTE TESTS
# ============================================================================


def test_missing_route_graceful_fallback(caplog):
    """When a node is unknown, fallback_enabled=True returns default_fallback_minutes and logs warning."""
    client = RoutingClient(default_fallback_minutes=12.5, fallback_enabled=True)
    with caplog.at_level(logging.WARNING):
        result = client.walk_minutes("unknown_node_1", "unknown_node_2")

    assert result == 12.5
    assert "Explicit routing fallback applied" in caplog.text


def test_missing_route_strict_mode_raises_error():
    """When fallback_enabled=False, unknown routes raise RouteNotFoundError."""
    client = RoutingClient(fallback_enabled=False)
    with pytest.raises(RouteNotFoundError, match="No route or coordinates found"):
        client.walk_minutes("unknown_node_1", "unknown_node_2")


# ============================================================================
# INVALID INPUT TESTS
# ============================================================================


@pytest.mark.parametrize("invalid_id", ["", "   ", None, 123, []])
def test_invalid_origin_node_id(invalid_id):
    """Invalid origin ID raises InvalidRouteError."""
    client = RoutingClient()
    with pytest.raises(
        InvalidRouteError, match="Parameter 'origin_node_id' must be a non-empty string"
    ):
        client.walk_minutes(invalid_id, SPACE_MTN_ID)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid_id", ["", "   ", None, 123, []])
def test_invalid_destination_node_id(invalid_id):
    """Invalid destination ID raises InvalidRouteError."""
    client = RoutingClient()
    with pytest.raises(
        InvalidRouteError,
        match="Parameter 'destination_node_id' must be a non-empty string",
    ):
        client.walk_minutes(SPACE_MTN_ID, invalid_id)  # type: ignore[arg-type]


# ============================================================================
# PLANNING CORE / PARKGRAPH INTEGRATION TESTS
# ============================================================================


def test_park_graph_delegates_to_routing_port():
    """Planning core (ParkGraph) delegates walk_minutes to RoutingPort."""
    routing = RoutingClient()
    graph = ParkGraph(routing=routing)
    assert graph.walk_minutes(SPACE_MTN_ID, TRON_ID) == 2.5
    assert graph.walk_minutes(SPACE_MTN_ID, SPACE_MTN_ID) == 0.0


def test_park_graph_unconfigured_routing_raises_runtime_error():
    """ParkGraph without configured RoutingPort raises RuntimeError."""
    graph = ParkGraph(routing=None)
    with pytest.raises(RuntimeError, match="RoutingPort has not been configured"):
        graph.walk_minutes(SPACE_MTN_ID, TRON_ID)


class DummyFakeRouting:
    """Mock routing implementation demonstrating port polymorphism."""

    def walk_minutes(self, origin_node_id: str, destination_node_id: str) -> float:
        return 42.0


def test_park_graph_with_alternative_routing_port():
    """ParkGraph works with any implementation satisfying RoutingPort."""
    fake_routing = DummyFakeRouting()
    _accepts_port(fake_routing)  # type-check
    graph = ParkGraph(routing=fake_routing)
    assert graph.walk_minutes("nodeA", "nodeB") == 42.0
