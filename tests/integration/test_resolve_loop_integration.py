"""Integration tests for PlannerResolveLoop (P0-21).

Uses real GreedyInsertionOptimizer and real ConstraintChecker without mocks.
Verifies:
  - Unavailable must-do (status DOWN) returns a valid plan with unmet_must_do populated.
  - Infeasible scenario returns an invalid plan (valid=False).
"""

from __future__ import annotations

from datetime import datetime

from parkmind.core.contracts import (
    PARK_TZ,
    Attraction,
    AttractionCategory,
    AttractionStatus,
    CoverageReport,
    Guest,
    GuestRole,
    LiveContext,
    Park,
    PartyConstraints,
    WaitEstimate,
)
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.planning.resolve_loop import PlannerResolveLoop

DAY = datetime(2026, 9, 16, tzinfo=PARK_TZ)


class _FlatRouting:
    """Flat 5-minute walking time between any distinct nodes."""

    def __init__(self, minutes: float = 5.0) -> None:
        self._minutes = minutes

    def walk_minutes(self, origin: str, destination: str) -> float:
        return 0.0 if origin == destination else self._minutes


def _park() -> Park:
    return Park(
        park_id="mk-test",
        name="Magic Kingdom Test",
        opening_time=DAY.replace(hour=9, minute=0),
        closing_time=DAY.replace(hour=21, minute=0),
    )


def _catalog() -> list[Attraction]:
    return [
        Attraction(
            node_id="space_mountain",
            name="Space Mountain",
            category=AttractionCategory.THRILL,
            height_restriction_cm=112.0,
            duration_minutes=15.0,
            typical_wait_minutes=30.0,
        ),
        Attraction(
            node_id="big_thunder",
            name="Big Thunder Mountain Railroad",
            category=AttractionCategory.THRILL,
            height_restriction_cm=102.0,
            duration_minutes=15.0,
            typical_wait_minutes=20.0,
        ),
        Attraction(
            node_id="peter_pan",
            name="Peter Pan's Flight",
            category=AttractionCategory.FAMILY,
            height_restriction_cm=None,
            duration_minutes=15.0,
            typical_wait_minutes=15.0,
        ),
    ]


def _live_context(
    statuses: dict[str, AttractionStatus], waits: dict[str, float] | None = None
) -> LiveContext:
    wait_estimates = {
        nid: WaitEstimate(
            attraction_id=nid,
            wait_minutes=w,
            status=statuses.get(nid, AttractionStatus.OPERATING),
        )
        for nid, w in (waits or {}).items()
    }
    return LiveContext(
        snapshot_id="snap-integration-test",
        retrieved_at=DAY.replace(hour=9, minute=0),
        waits=wait_estimates,
        statuses=statuses,
        showtimes={},
        coverage=CoverageReport(
            required_attractions_covered=True,
            required_shows_covered=True,
            weather_covered=True,
            accessibility_checks_complete=True,
        ),
    )


def test_must_do_down_yields_valid_plan_with_unmet_must_do() -> None:
    """An attraction in must_do that is DOWN for the horizon results in valid=True with unmet_must_do."""
    routing = _FlatRouting(minutes=5.0)
    graph = ParkGraph.from_sources(
        routing=routing, park=_park(), attractions=_catalog()
    )
    optimizer = GreedyInsertionOptimizer(park_graph=graph)
    checker = ConstraintChecker()
    loop = PlannerResolveLoop(optimizer=optimizer, checker=checker, max_attempts=3)

    # space_mountain is DOWN, big_thunder and peter_pan are OPERATING
    statuses = {
        "space_mountain": AttractionStatus.DOWN,
        "big_thunder": AttractionStatus.OPERATING,
        "peter_pan": AttractionStatus.OPERATING,
    }
    waits = {
        "space_mountain": 0.0,
        "big_thunder": 15.0,
        "peter_pan": 10.0,
    }
    context = _live_context(statuses=statuses, waits=waits)

    guest = Guest(guest_id="adult_1", role=GuestRole.ADULT, height_cm=180.0)
    constraints = PartyConstraints(
        party_size=1,
        guests=[guest],
        must_do=["space_mountain", "big_thunder"],
        avoid=[],
        departure_time=DAY.replace(hour=18, minute=0),
        constraints_version=1,
    )

    utilities = {
        "space_mountain": 5.0,
        "big_thunder": 4.0,
        "peter_pan": 2.0,
    }

    result = loop.resolve(
        constraints=constraints,
        context=context,
        utilities=utilities,
        now=DAY.replace(hour=9, minute=0),
        park=_park(),
        catalog=_catalog(),
    )

    assert result.valid is True
    assert result.plan is not None
    assert "space_mountain" in result.unmet_must_do
    # Big Thunder should have been scheduled successfully
    scheduled_nodes = {s.node_id for s in result.plan.stops}
    assert "big_thunder" in scheduled_nodes
    assert "space_mountain" not in scheduled_nodes
    assert result.fatal_error is None


def test_infeasible_scenario_returns_invalid_plan() -> None:
    """A stale context without a context_reloader fails closed with valid=False."""
    routing = _FlatRouting(minutes=5.0)
    graph = ParkGraph.from_sources(
        routing=routing, park=_park(), attractions=_catalog()
    )
    optimizer = GreedyInsertionOptimizer(park_graph=graph)
    checker = ConstraintChecker()
    loop = PlannerResolveLoop(optimizer=optimizer, checker=checker, max_attempts=3)

    # Contexto con fecha vieja (1 hora antes de 'now') para disparar DATA_FRESHNESS
    statuses = {"space_mountain": AttractionStatus.OPERATING}
    waits = {"space_mountain": 10.0}
    context = _live_context(statuses=statuses, waits=waits)
    context = context.model_copy(
        update={"retrieved_at": DAY.replace(hour=8, minute=0)}
    )

    guest = Guest(guest_id="adult_1", role=GuestRole.ADULT, height_cm=180.0)
    constraints = PartyConstraints(
        party_size=1,
        guests=[guest],
        must_do=["space_mountain"],
        avoid=[],
        departure_time=DAY.replace(hour=18, minute=0),
        constraints_version=1,
    )

    # Al pasar context_reloader=None con un contexto viejo, debe fallar cerrado (valid=False)
    result = loop.resolve(
        constraints=constraints,
        context=context,
        utilities={"space_mountain": 10.0},
        now=DAY.replace(hour=9, minute=0),
        park=_park(),
        catalog=_catalog(),
        context_reloader=None,
    )

    assert result.valid is False
    assert result.plan is None
    assert result.fatal_error is not None
