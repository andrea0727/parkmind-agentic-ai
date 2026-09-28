"""Unit tests for ParkGraph"""

from datetime import datetime, timedelta

import factories

from parkmind.core.contracts import PARK_TZ
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import RoutingPort

HUB_ID = "90d79335-c907-4069-a021-d0fe1ec73ae2"
SPACE_MTN_ID = "b2260923-9315-40fd-9c6b-44dd811dbe64"


class DummyRouting:
    def walk_minutes(self, origin_node_id: str, destination_node_id: str) -> float:
        return 5.0


def _accepts_port(port: RoutingPort) -> None:
    """mypy-only check."""


# ============================================================================
# WALK_MINUTES
# ============================================================================


def test_walk_minutes_delegates_to_routing():
    graph = ParkGraph(routing=DummyRouting())
    assert graph.walk_minutes(HUB_ID, SPACE_MTN_ID) == 5.0


def test_walk_minutes_without_routing_raises():
    graph = ParkGraph()
    try:
        graph.walk_minutes(HUB_ID, SPACE_MTN_ID)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass


# ============================================================================
# OPEN_AT (temporal edge cases)
# ============================================================================


def test_open_at_within_window():
    park = factories.park()
    graph = ParkGraph(park=park)
    midday = park.opening_time + timedelta(hours=2)
    assert graph.open_at(SPACE_MTN_ID, midday) is True


def test_open_at_exactly_at_opening_boundary():
    park = factories.park()
    graph = ParkGraph(park=park)
    assert graph.open_at(SPACE_MTN_ID, park.opening_time) is True


def test_open_at_exactly_at_closing_boundary():
    park = factories.park()
    graph = ParkGraph(park=park)
    assert graph.open_at(SPACE_MTN_ID, park.closing_time) is True


def test_open_at_before_opening():
    park = factories.park()
    graph = ParkGraph(park=park)
    before = park.opening_time - timedelta(minutes=1)
    assert graph.open_at(SPACE_MTN_ID, before) is False


def test_open_at_after_closing():
    park = factories.park()
    graph = ParkGraph(park=park)
    after = park.closing_time + timedelta(minutes=1)
    assert graph.open_at(SPACE_MTN_ID, after) is False


def test_open_at_without_park_raises():
    graph = ParkGraph()
    try:
        graph.open_at(SPACE_MTN_ID, datetime(2026, 9, 16, 12, 0, tzinfo=PARK_TZ))
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass


# ============================================================================
# SHOWTIMES
# ============================================================================


def test_showtimes_returns_known_times():
    now = datetime(2026, 9, 16, 10, 0, tzinfo=PARK_TZ)
    times = [now + timedelta(hours=2), now + timedelta(hours=4)]
    graph = ParkGraph(showtimes={"show_1": times})
    assert graph.showtimes("show_1") == times


def test_showtimes_returns_empty_list_for_unknown_show():
    graph = ParkGraph()
    assert graph.showtimes("unknown_show") == []


# ============================================================================
# RESOLVE_LOCATION
# ============================================================================


def test_resolve_location_known_alias_with_prefix():
    graph = ParkGraph()
    assert graph.resolve_location("near Frontierland") == "land:frontierland"


def test_resolve_location_known_alias_without_prefix():
    graph = ParkGraph()
    assert graph.resolve_location("Tomorrowland") == "land:tomorrowland"


def test_resolve_location_unknown_alias_returns_none():
    graph = ParkGraph()
    assert graph.resolve_location("near Hogwarts") is None


# ============================================================================
# FILTER_ELIGIBLE
# ============================================================================


def test_filter_eligible_excludes_by_height():
    tall_ride = factories.attraction(node_id="a1", height_restriction_cm=112)
    graph = ParkGraph(attractions=[tall_ride])
    assert graph.filter_eligible(["a1"], guest_height_cm=100) == []
    assert graph.filter_eligible(["a1"], guest_height_cm=120) == ["a1"]


def test_filter_eligible_excludes_outdoor_when_not_allowed():
    outdoor_attraction = factories.attraction(
        node_id="a1", outdoor=True, height_restriction_cm=None
    )
    graph = ParkGraph(attractions=[outdoor_attraction])
    assert graph.filter_eligible(["a1"], outdoor_ok=False) == []
    assert graph.filter_eligible(["a1"], outdoor_ok=True) == ["a1"]


def test_filter_eligible_keeps_unknown_node_ids():
    graph = ParkGraph()
    assert graph.filter_eligible(["land:frontierland"], guest_height_cm=100) == [
        "land:frontierland"
    ]
