"""Planning time stays small as the number of must-dos grows."""

import statistics
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest

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
from performance.perf_budgets import (
    PLANNER_BUILD_SECONDS,
    PLANNER_CHECK_SECONDS,
    PLANNER_MUST_DO_COUNTS,
)

DAY = datetime(2026, 9, 16, tzinfo=PARK_TZ)
CATALOG_SIZE = 14
REPEATS = 5


class _FlatRouting:
    def walk_minutes(self, origin: str, destination: str) -> float:
        return 0.0 if origin == destination else 5.0


def _park() -> Park:
    return Park(
        park_id="park-1",
        name="Test Park",
        opening_time=DAY.replace(hour=9),
        closing_time=DAY.replace(hour=22),
    )


def _catalog() -> list[Attraction]:
    return [
        Attraction(
            node_id=f"ride-{n:02d}",
            name=f"Ride {n:02d}",
            category=AttractionCategory.FAMILY,
            height_restriction_cm=None,
            typical_wait_minutes=10,
            outdoor=False,
        )
        for n in range(CATALOG_SIZE)
    ]


def _context(catalog: list[Attraction]) -> LiveContext:
    return LiveContext(
        snapshot_id="snap-perf",
        retrieved_at=DAY.replace(hour=9),
        waits={
            a.node_id: WaitEstimate(
                attraction_id=a.node_id,
                wait_minutes=10.0,
                status=AttractionStatus.OPERATING,
            )
            for a in catalog
        },
        statuses={a.node_id: AttractionStatus.OPERATING for a in catalog},
        showtimes={},
        coverage=CoverageReport(
            required_attractions_covered=True,
            required_shows_covered=True,
            weather_covered=True,
            accessibility_checks_complete=True,
        ),
    )


def _constraints(must_do: list[str]) -> PartyConstraints:
    guests = [Guest(guest_id="g1", role=GuestRole.ADULT, height_cm=175.0)]
    return PartyConstraints(
        party_size=1,
        guests=guests,
        must_do=must_do,
        avoid=[],
        departure_time=DAY.replace(hour=22),
        constraints_version=1,
    )


def _median_seconds(action: Callable[[], Any]) -> tuple[Any, float]:
    durations = []
    result = None
    for _ in range(REPEATS):
        started = time.perf_counter()
        result = action()
        durations.append(time.perf_counter() - started)
    return result, statistics.median(durations)


@pytest.fixture
def catalog() -> list[Attraction]:
    return _catalog()


@pytest.mark.parametrize("must_do_count", PLANNER_MUST_DO_COUNTS)
def test_building_a_plan_with_many_must_dos_stays_within_budget(
    catalog: list[Attraction], must_do_count: int
) -> None:
    park = _park()
    graph = ParkGraph.from_sources(
        routing=_FlatRouting(), park=park, attractions=catalog
    )
    optimizer = GreedyInsertionOptimizer(park_graph=graph)
    must_do = [a.node_id for a in catalog[:must_do_count]]

    plan, seconds = _median_seconds(
        lambda: optimizer.build_plan(
            constraints=_constraints(must_do),
            context=_context(catalog),
            utilities={a.node_id: 1.0 for a in catalog},
            park=park,
            catalog=catalog,
        )
    )

    planned = {s.node_id for s in plan.stops}
    assert set(must_do) <= planned, (
        f"{must_do_count} must-dos, missing {set(must_do) - planned}"
    )
    assert seconds < PLANNER_BUILD_SECONDS, (
        f"{must_do_count} must-dos: median build {seconds * 1000:.1f} ms "
        f"(budget {PLANNER_BUILD_SECONDS * 1000:.0f} ms)"
    )


@pytest.mark.parametrize("must_do_count", PLANNER_MUST_DO_COUNTS)
def test_checking_a_plan_with_many_must_dos_stays_within_budget(
    catalog: list[Attraction], must_do_count: int
) -> None:
    park = _park()
    context = _context(catalog)
    graph = ParkGraph.from_sources(
        routing=_FlatRouting(), park=park, attractions=catalog
    )
    must_do = [a.node_id for a in catalog[:must_do_count]]
    constraints = _constraints(must_do)
    plan = GreedyInsertionOptimizer(park_graph=graph).build_plan(
        constraints=constraints,
        context=context,
        utilities={a.node_id: 1.0 for a in catalog},
        park=park,
        catalog=catalog,
    )
    checker = ConstraintChecker()
    by_id = {a.node_id: a for a in catalog}
    now = DAY.replace(hour=9) + timedelta(minutes=1)

    result, seconds = _median_seconds(
        lambda: checker.check(plan, constraints, [], by_id, park, context, now)
    )

    assert not [v for v in result.violations if v.rule.value == "MUST_DO"]
    assert seconds < PLANNER_CHECK_SECONDS, (
        f"{must_do_count} must-dos: median check {seconds * 1000:.1f} ms "
        f"(budget {PLANNER_CHECK_SECONDS * 1000:.0f} ms)"
    )
