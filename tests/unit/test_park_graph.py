"""Unit tests for ParkGraph (planning topology)."""

from datetime import datetime, timedelta
from typing import Any

import pytest

from parkmind.core.contracts import (
    Attraction,
    AttractionCategory,
    AttractionStatus,
    CoverageReport,
    LiveContext,
    Park,
)
from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.land_reference_data import (
    MAGIC_KINGDOM_LAND_ALIASES,
    MAGIC_KINGDOM_LANDS,
)
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import RoutingPort

# ============================================================================
# FAKES & FIXTURES
# ============================================================================


class FakeRouting:
    """In-memory RoutingPort returning a fixed table; unknown pairs -> 5 min."""

    def __init__(self, table: dict[tuple[str, str], float] | None = None) -> None:
        self.table = table or {}
        self.calls: list[tuple[str, str]] = []

    def walk_minutes(self, origin_node_id: str, destination_node_id: str) -> float:
        self.calls.append((origin_node_id, destination_node_id))
        return self.table.get((origin_node_id, destination_node_id), 5.0)


OPENING = datetime(2026, 10, 1, 9, 0, tzinfo=PARK_TZ)
CLOSING = datetime(2026, 10, 1, 22, 0, tzinfo=PARK_TZ)


def _park(**overrides: Any) -> Park:
    fields: dict[str, Any] = {
        "park_id": "mk",
        "name": "Magic Kingdom Park",
        "opening_time": OPENING,
        "closing_time": CLOSING,
        "outdoor": True,
    }
    return Park(**{**fields, **overrides})


def _attraction(node_id: str, **overrides: Any) -> Attraction:
    fields: dict[str, Any] = {
        "node_id": node_id,
        "name": node_id.upper(),
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": False,
        "land": "Fantasyland",
    }
    return Attraction(**{**fields, **overrides})


def _coverage() -> CoverageReport:
    return CoverageReport(
        required_attractions_covered=True,
        required_shows_covered=True,
        weather_covered=True,
        accessibility_checks_complete=True,
    )


def _live(
    *,
    showtimes: dict[str, list[datetime]] | None = None,
    statuses: dict[str, AttractionStatus] | None = None,
) -> LiveContext:
    return LiveContext(
        snapshot_id="snap-1",
        retrieved_at=OPENING,
        waits={},
        statuses=statuses or {},
        showtimes=showtimes or {},
        weather=[],
        accessibility_results=[],
        coverage=_coverage(),
    )


# ============================================================================
# CONSTRUCTION & ROUTING DELEGATION
# ============================================================================


def test_from_sources_requires_routing() -> None:
    """ParkGraph still fails fast when routing is None."""
    with pytest.raises(TypeError, match="requires a valid RoutingPort"):
        ParkGraph.from_sources(routing=None, park=_park(), attractions=())  # type: ignore[arg-type]


def test_walk_minutes_delegates_to_routing_port() -> None:
    """walk_minutes forwards to the injected RoutingPort unchanged."""
    routing = FakeRouting({("a", "b"): 7.5})
    graph = ParkGraph.from_sources(routing=routing, park=_park(), attractions=())
    assert graph.walk_minutes("a", "b") == 7.5
    assert routing.calls == [("a", "b")]


def test_fake_routing_satisfies_port_structurally() -> None:
    """The FakeRouting fixture is accepted where RoutingPort is expected."""

    def accept(_: RoutingPort) -> None: ...

    accept(FakeRouting())


# ============================================================================
# open_at — PARK OPENING WINDOW + STATUSES
# ============================================================================


@pytest.mark.parametrize(
    "t,expected",
    [
        (OPENING - timedelta(minutes=1), False),  # before opening
        (OPENING, True),  # exact open (inclusive)
        (OPENING + timedelta(hours=5), True),  # inside
        (CLOSING - timedelta(minutes=1), True),  # just before close
        (CLOSING, False),  # exact close (exclusive — no plannable stop here)
        (CLOSING + timedelta(minutes=1), False),  # after closing
    ],
)
def test_open_at_respects_park_window(t: datetime, expected: bool) -> None:
    """A known OPERATING node is open iff t is in [opening_time, closing_time)."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=[_attraction("a")],
        live_context=_live(statuses={"a": AttractionStatus.OPERATING}),
    )
    assert graph.open_at("a", t) is expected


@pytest.mark.parametrize(
    "status,expected",
    [
        (AttractionStatus.OPERATING, True),
        (AttractionStatus.DOWN, False),
        (AttractionStatus.CLOSED, False),
        (AttractionStatus.REFURBISHMENT, False),
    ],
)
def test_open_at_respects_live_status(status: AttractionStatus, expected: bool) -> None:
    """Any non-OPERATING live status makes open_at False even inside the window."""
    noon = OPENING + timedelta(hours=3)
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=[_attraction("a")],
        live_context=_live(statuses={"a": status}),
    )
    assert graph.open_at("a", noon) is expected


def test_open_at_unknown_node_returns_false() -> None:
    """Unknown node ids are an explicit miss, not an exception."""
    graph = ParkGraph.from_sources(routing=FakeRouting(), park=_park(), attractions=())
    assert graph.open_at("ghost", OPENING) is False


def test_open_at_fails_closed_when_status_is_unknown() -> None:
    """A catalog node absent from ``statuses`` is treated as not open.

    Mirrors ConstraintChecker rule 1 ("status unknown ... failing closed")
    and Optimizer (only explicit OPERATING nodes are admitted): open_at must
    not be the one place in the core that defaults an unknown status to
    OPERATING, or planners would propose stops the checker then rejects.
    """
    noon = OPENING + timedelta(hours=3)
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=[_attraction("a")]
    )
    assert graph.open_at("a", noon) is False


# ============================================================================
# showtimes
# ============================================================================


def test_showtimes_returns_known_times() -> None:
    """Known show id yields the times from LiveContext."""
    times = [OPENING + timedelta(hours=h) for h in (2, 5, 8)]
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=[_attraction("show")],
        live_context=_live(showtimes={"show": times}),
    )
    assert graph.showtimes("show") == times


def test_showtimes_unknown_show_returns_empty_list() -> None:
    """Unknown show id yields [] rather than raising."""
    graph = ParkGraph.from_sources(routing=FakeRouting(), park=_park(), attractions=())
    assert graph.showtimes("mystery") == []


def test_showtimes_returns_fresh_copy() -> None:
    """Mutating the returned list does not corrupt the graph's state."""
    times = [OPENING + timedelta(hours=2)]
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=[_attraction("show")],
        live_context=_live(showtimes={"show": times}),
    )
    first = graph.showtimes("show")
    first.append(OPENING)
    assert graph.showtimes("show") == times


# ============================================================================
# resolve_location — alias registry
# ============================================================================


@pytest.mark.parametrize(
    "query,expected_land",
    [
        ("Frontierland", "Frontierland"),
        ("frontierland", "Frontierland"),
        ("  FRONTIER  LAND  ", "Frontierland"),
        ("near Frontierland", "Frontierland"),
        ("in frontierland", "Frontierland"),
        ("close to Fantasyland", "Fantasyland"),
        ("Tomorrowland", "Tomorrowland"),
        ("Main Street, U.S.A.", "Main Street, U.S.A."),
    ],
)
def test_resolve_location_returns_representative_node(
    query: str, expected_land: str
) -> None:
    """Known aliases resolve to the alphabetically-first node of the land."""
    attractions = [
        _attraction("f1", name="Big Thunder", land="Frontierland"),
        _attraction("f2", name="Country Bears", land="Frontierland"),
        _attraction("ftl1", name="Peter Pan", land="Fantasyland"),
        _attraction("ftl2", name="Dumbo", land="Fantasyland"),
        _attraction("tmr", name="Space Mountain", land="Tomorrowland"),
        _attraction("ms", name="Main Street Vehicles", land="Main Street, U.S.A."),
    ]
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=attractions,
        land_aliases=MAGIC_KINGDOM_LAND_ALIASES,
    )
    resolved = graph.resolve_location(query)
    assert resolved is not None
    assert graph.land_of[resolved] == expected_land


def test_resolve_location_unknown_alias_returns_none() -> None:
    """Aliases not in the registry are an explicit miss."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=[_attraction("a")],
        land_aliases=MAGIC_KINGDOM_LAND_ALIASES,
    )
    assert graph.resolve_location("Narnia") is None
    assert graph.resolve_location("") is None


def test_resolve_location_known_land_without_nodes_returns_none() -> None:
    """A registered land with no catalog nodes still returns None (explicit miss)."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=[],
        land_aliases=MAGIC_KINGDOM_LAND_ALIASES,
    )
    assert graph.resolve_location("Frontierland") is None


def test_resolve_location_picks_alphabetically_first_node() -> None:
    """The returned representative node is deterministic (sort by Attraction.name)."""
    attractions = [
        _attraction("z", name="Zebra Ride", land="Frontierland"),
        _attraction("a", name="Alligator Swamp", land="Frontierland"),
        _attraction("m", name="Mountain Climb", land="Frontierland"),
    ]
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=attractions,
        land_aliases=MAGIC_KINGDOM_LAND_ALIASES,
    )
    assert graph.resolve_location("Frontierland") == "a"


# ============================================================================
# filter_candidates — eligibility metadata
# ============================================================================


def _catalog() -> list[Attraction]:
    """A small, varied catalog used by eligibility-filter tests."""
    return [
        _attraction(
            "thrill_outdoor_tall",
            category=AttractionCategory.THRILL,
            height_restriction_cm=122,
            outdoor=True,
            land="Tomorrowland",
        ),
        _attraction(
            "thrill_indoor",
            category=AttractionCategory.THRILL,
            height_restriction_cm=102,
            outdoor=False,
            land="Tomorrowland",
        ),
        _attraction(
            "family_outdoor",
            category=AttractionCategory.FAMILY,
            height_restriction_cm=None,
            outdoor=True,
            land="Fantasyland",
        ),
        _attraction(
            "dark_ride",
            category=AttractionCategory.DARK_RIDE,
            height_restriction_cm=None,
            outdoor=False,
            land="Fantasyland",
        ),
        _attraction(
            "show",
            category=AttractionCategory.SHOW,
            height_restriction_cm=None,
            outdoor=False,
            land="Frontierland",
        ),
    ]


def test_filter_candidates_no_filters_returns_all_sorted() -> None:
    """With no filters set, every node id is returned, sorted."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=_catalog()
    )
    result = graph.filter_candidates()
    assert result == sorted(a.node_id for a in _catalog())


def test_filter_candidates_height_excludes_tall_restrictions() -> None:
    """guest_height_cm filters out anything whose restriction exceeds the guest reach."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=_catalog()
    )
    assert "thrill_outdoor_tall" not in graph.filter_candidates(guest_height_cm=110)
    assert "thrill_indoor" in graph.filter_candidates(guest_height_cm=110)
    # A None restriction always passes.
    assert "family_outdoor" in graph.filter_candidates(guest_height_cm=50)


def test_filter_candidates_excludes_categories() -> None:
    """exclude_categories drops attractions in any excluded category."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=_catalog()
    )
    result = graph.filter_candidates(exclude_categories={AttractionCategory.THRILL})
    assert "thrill_outdoor_tall" not in result
    assert "thrill_indoor" not in result
    assert "family_outdoor" in result


def test_filter_candidates_outdoor_filter() -> None:
    """outdoor_ok=False drops outdoor attractions only."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=_catalog()
    )
    result = graph.filter_candidates(outdoor_ok=False)
    assert "thrill_outdoor_tall" not in result
    assert "family_outdoor" not in result
    assert "thrill_indoor" in result
    assert "dark_ride" in result


def test_filter_candidates_land_filter() -> None:
    """land=X restricts to that canonical land."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=_catalog()
    )
    assert graph.filter_candidates(land="Fantasyland") == sorted(
        ["family_outdoor", "dark_ride"]
    )


def test_filter_candidates_combined_filters() -> None:
    """All filters AND together."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=_catalog()
    )
    # Guest short (90 cm), no outdoors, Fantasyland only.
    result = graph.filter_candidates(
        guest_height_cm=90,
        outdoor_ok=False,
        land="Fantasyland",
    )
    assert result == ["dark_ride"]


# ============================================================================
# from_sources — composition
# ============================================================================


def test_from_sources_groups_nodes_by_land() -> None:
    """nodes_by_land is keyed by canonical land name and sorted by Attraction.name."""
    attractions = [
        _attraction("z", name="Zebra", land="Frontierland"),
        _attraction("a", name="Alligator", land="Frontierland"),
        _attraction("t", name="TRON", land="Tomorrowland"),
    ]
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=attractions
    )
    assert graph.nodes_by_land["Frontierland"] == ("a", "z")
    assert graph.nodes_by_land["Tomorrowland"] == ("t",)


def test_from_sources_buckets_attractions_without_land_as_unknown() -> None:
    """A missing land does not break the graph; it goes to 'Unknown' bucket."""
    attractions = [_attraction("orphan", land=None)]
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=attractions
    )
    assert graph.land_of["orphan"] == "Unknown"
    assert graph.nodes_by_land["Unknown"] == ("orphan",)


def test_from_sources_without_live_context_leaves_showtimes_and_statuses_empty() -> (
    None
):
    """When no LiveContext is passed, showtimes and statuses default to empty."""
    graph = ParkGraph.from_sources(
        routing=FakeRouting(), park=_park(), attractions=[_attraction("a")]
    )
    assert graph.showtimes_by_node == {}
    assert graph.statuses == {}


def test_from_sources_hydrates_from_live_context() -> None:
    """showtimes and statuses from LiveContext end up on the graph."""
    showtimes = {"show": [OPENING + timedelta(hours=2)]}
    statuses = {"a": AttractionStatus.DOWN}
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=[_attraction("a"), _attraction("show")],
        live_context=_live(showtimes=showtimes, statuses=statuses),
    )
    assert graph.showtimes_by_node["show"] == tuple(showtimes["show"])
    assert graph.statuses["a"] is AttractionStatus.DOWN


def test_from_sources_returns_immutable_indexes() -> None:
    """Indexes are read-only mappings, not plain dicts callers could mutate.

    ``frozen=True`` only stops reassigning a ``ParkGraph`` attribute; it does
    not stop in-place mutation of a mutable object that attribute points to.
    ``from_sources`` must wrap every index in ``MappingProxyType`` so the
    class docstring's "its indexes are immutable" is actually true.
    """
    graph = ParkGraph.from_sources(
        routing=FakeRouting(),
        park=_park(),
        attractions=[_attraction("a")],
        live_context=_live(statuses={"a": AttractionStatus.OPERATING}),
        land_aliases=MAGIC_KINGDOM_LAND_ALIASES,
    )
    for index in (
        graph.attractions,
        graph.land_of,
        graph.nodes_by_land,
        graph.land_aliases,
        graph.showtimes_by_node,
        graph.statuses,
    ):
        with pytest.raises(TypeError):
            index["__mutate__"] = None  # type: ignore[index]


# ============================================================================
# Alias registry sanity (fuente de verdad en services/clients)
# ============================================================================


def test_land_aliases_only_map_to_canonical_lands() -> None:
    """Every alias in the curated registry targets a known canonical land."""
    assert set(MAGIC_KINGDOM_LAND_ALIASES.values()) <= MAGIC_KINGDOM_LANDS
