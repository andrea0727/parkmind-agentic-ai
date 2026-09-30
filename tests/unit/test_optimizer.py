"""Unit tests for GreedyInsertionOptimizer (P0-19).

Coverage:
  - Basic greedy flow with chronological ordering
  - Must-do priority and avoid filtering
  - Graceful degradation for unreachable must-dos [C18]
  - Lunch-window meal insertion at a restaurant node
  - REST insertion driven by rest_frequency_minutes [C19]
  - Departure-time cutoff
  - Per-guest satisfaction normalization [C20]
  - Empty candidate set (no attractions available)
  - Show anchor insertion from showtimes
"""

from __future__ import annotations

from datetime import datetime

import pytest

from parkmind.core.contracts import (
    PARK_TZ,
    AccessibilityRequirements,
    Attraction,
    AttractionCategory,
    AttractionStatus,
    CoverageReport,
    EventThresholds,
    FairnessConfig,
    GroupObjective,
    Guest,
    GuestRole,
    HardConstraintSet,
    LiveContext,
    Park,
    PartyConstraints,
    StopKind,
    TimeWindow,
    WaitEstimate,
)
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.park_graph import ParkGraph

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAY = datetime(2026, 9, 16, tzinfo=PARK_TZ)

A1 = "attraction-1"
A2 = "attraction-2"
A3 = "attraction-3"
A4 = "attraction-4"  # will be DOWN
A5 = "attraction-5"  # avoided
SHOW_1 = "show-1"
REST_1 = "rest-area-1"
RESTAURANT_1 = "restaurant-1"


# ---------------------------------------------------------------------------
# Fake routing adapter (deterministic 5-min between any pair)
# ---------------------------------------------------------------------------
class _FlatRoutingPort:
    """Every pair of nodes is 5 walking-minutes apart."""

    def __init__(self, minutes: float = 5.0) -> None:
        self._minutes = minutes

    def walk_minutes(self, origin: str, destination: str) -> float:
        return 0.0 if origin == destination else self._minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _park(opening_hour: int = 9, closing_hour: int = 22) -> Park:
    return Park(
        park_id="park-1",
        name="Test Park",
        opening_time=DAY.replace(hour=opening_hour),
        closing_time=DAY.replace(hour=closing_hour),
    )


def _guests(*ids: str) -> list[Guest]:
    return [Guest(guest_id=gid, role=GuestRole.ADULT, height_cm=175.0) for gid in ids]


def _constraints(
    guests: list[Guest] | None = None,
    must_do: list[str] | None = None,
    avoid: list[str] | None = None,
    lunch_window: TimeWindow | None = None,
    departure_hour: int = 22,
) -> PartyConstraints:
    return PartyConstraints(
        party_size=len(guests) if guests else 1,
        guests=guests or _guests("g1"),
        must_do=must_do or [],
        avoid=avoid or [],
        lunch_window=lunch_window,
        departure_time=DAY.replace(hour=departure_hour),
        constraints_version=1,
    )


def _live_context(
    waits: dict[str, float] | None = None,
    statuses: dict[str, AttractionStatus] | None = None,
    showtimes: dict[str, list[datetime]] | None = None,
) -> LiveContext:
    wait_estimates = {
        nid: WaitEstimate(
            attraction_id=nid,
            wait_minutes=w,
            status=AttractionStatus.OPERATING,
        )
        for nid, w in (waits or {}).items()
    }
    return LiveContext(
        snapshot_id="snap-test",
        retrieved_at=DAY.replace(hour=9),
        waits=wait_estimates,
        statuses=statuses or {},
        showtimes=showtimes or {},
        coverage=CoverageReport(
            required_attractions_covered=True,
            required_shows_covered=True,
            weather_covered=True,
            accessibility_checks_complete=True,
        ),
    )


def _catalog() -> list[Attraction]:
    return [
        Attraction(
            node_id=A1,
            name="Ride Alpha",
            category=AttractionCategory.THRILL,
            height_restriction_cm=None,
            typical_wait_minutes=20,
            outdoor=False,
        ),
        Attraction(
            node_id=A2,
            name="Ride Beta",
            category=AttractionCategory.FAMILY,
            height_restriction_cm=None,
            typical_wait_minutes=15,
            outdoor=False,
        ),
        Attraction(
            node_id=A3,
            name="Ride Gamma",
            category=AttractionCategory.DARK_RIDE,
            height_restriction_cm=112,
            typical_wait_minutes=30,
            outdoor=False,
        ),
        Attraction(
            node_id=A4,
            name="Ride Delta (Down)",
            category=AttractionCategory.THRILL,
            height_restriction_cm=None,
            typical_wait_minutes=10,
            outdoor=True,
        ),
        Attraction(
            node_id=A5,
            name="Ride Epsilon (Avoided)",
            category=AttractionCategory.WATER,
            height_restriction_cm=None,
            typical_wait_minutes=10,
            outdoor=True,
        ),
    ]


def _default_statuses() -> dict[str, AttractionStatus]:
    return {
        A1: AttractionStatus.OPERATING,
        A2: AttractionStatus.OPERATING,
        A3: AttractionStatus.OPERATING,
        A4: AttractionStatus.DOWN,
        A5: AttractionStatus.OPERATING,
    }


def _build_optimizer(walk_minutes: float = 5.0) -> GreedyInsertionOptimizer:
    routing = _FlatRoutingPort(minutes=walk_minutes)
    graph = ParkGraph(routing=routing)
    return GreedyInsertionOptimizer(park_graph=graph)


# ===================================================================
# Tests
# ===================================================================


class TestBasicFlow:
    """Core greedy insertion algorithm."""

    def test_basic_plan_with_three_attractions(self) -> None:
        """Produces a plan with stops in chronological order and consistent
        arrival/departure times."""
        optimizer = _build_optimizer()
        park = _park()
        utilities = {A1: 3.0, A2: 2.0, A3: 1.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            A2: AttractionStatus.OPERATING,
            A3: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 10.0, A2: 5.0, A3: 15.0},
            statuses=statuses,
        )

        plan = optimizer.build_plan(
            constraints=_constraints(),
            context=context,
            utilities=utilities,
            park=park,
            catalog=_catalog(),
        )

        assert len(plan.stops) >= 1
        assert plan.version == 1
        assert plan.provenance.optimizer_strategy == "greedy_insertion"

        # Chronological ordering
        for i in range(1, len(plan.stops)):
            assert plan.stops[i].arrival_time >= plan.stops[i - 1].departure_time

        # Each stop has departure > arrival
        for s in plan.stops:
            assert s.departure_time > s.arrival_time

    def test_total_wait_and_walking_are_consistent(self) -> None:
        """Totals match the sum of individual stops."""
        optimizer = _build_optimizer()
        park = _park()
        utilities = {A1: 3.0, A2: 2.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            A2: AttractionStatus.OPERATING,
        }
        context = _live_context(waits={A1: 10.0, A2: 5.0}, statuses=statuses)

        plan = optimizer.build_plan(
            constraints=_constraints(),
            context=context,
            utilities=utilities,
            park=park,
            catalog=_catalog(),
        )

        expected_wait = sum(
            s.expected_wait_minutes
            for s in plan.stops
            if s.kind == StopKind.ATTRACTION
        )
        assert plan.total_wait_minutes == pytest.approx(expected_wait)
        assert plan.total_walking_minutes >= 0


class TestMustDoAndAvoid:
    """Must-do and avoid constraint handling."""

    def test_must_do_inserted_first(self) -> None:
        """Must-do attractions appear before utility-sorted candidates."""
        optimizer = _build_optimizer()
        # A3 has the lowest utility but is must-do ⇒ should appear first
        utilities = {A1: 5.0, A2: 3.0, A3: 1.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            A2: AttractionStatus.OPERATING,
            A3: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 10.0, A2: 5.0, A3: 10.0},
            statuses=statuses,
        )

        plan = optimizer.build_plan(
            constraints=_constraints(must_do=[A3]),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
        )

        attraction_stops = [
            s for s in plan.stops if s.kind == StopKind.ATTRACTION
        ]
        assert len(attraction_stops) >= 1
        assert attraction_stops[0].node_id == A3

    def test_avoided_attraction_never_appears(self) -> None:
        """Attractions in the avoid list are excluded from all stops."""
        optimizer = _build_optimizer()
        utilities = {A1: 3.0, A5: 10.0}  # A5 has high utility but is avoided
        statuses = {
            A1: AttractionStatus.OPERATING,
            A5: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 10.0, A5: 5.0},
            statuses=statuses,
        )

        plan = optimizer.build_plan(
            constraints=_constraints(avoid=[A5]),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
        )

        stop_ids = {s.node_id for s in plan.stops}
        assert A5 not in stop_ids


class TestGracefulDegradation:
    """C18: must-dos that can't be met go to unmet_must_do, not exceptions."""

    def test_down_must_do_in_unmet(self) -> None:
        """A must-do that is DOWN is listed in unmet_must_do."""
        optimizer = _build_optimizer()
        utilities = {A1: 3.0, A4: 5.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            A4: AttractionStatus.DOWN,
        }
        context = _live_context(waits={A1: 10.0}, statuses=statuses)

        plan = optimizer.build_plan(
            constraints=_constraints(must_do=[A4]),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
        )

        assert A4 in plan.unmet_must_do
        stop_ids = {s.node_id for s in plan.stops}
        assert A4 not in stop_ids

    def test_must_do_exceeding_departure_in_unmet(self) -> None:
        """A must-do that can't fit before departure_time is listed in
        unmet_must_do."""
        optimizer = _build_optimizer()
        # Only 30 min window — walk (5) + wait (20) + duration (15) = 40 > 30
        park = _park(opening_hour=9, closing_hour=22)
        utilities = {A1: 3.0}
        statuses = {A1: AttractionStatus.OPERATING}
        context = _live_context(waits={A1: 20.0}, statuses=statuses)

        plan = optimizer.build_plan(
            constraints=_constraints(must_do=[A1], departure_hour=9),
            context=context,
            utilities=utilities,
            park=park,
            catalog=_catalog(),
        )

        # departure_time == opening_time ⇒ nothing fits
        assert A1 in plan.unmet_must_do


class TestLunchWindow:
    """Meal anchor inserted within lunch_window."""

    def test_meal_stop_inserted_at_restaurant(self) -> None:
        """A MEAL stop appears with the correct restaurant node_id."""
        optimizer = _build_optimizer()
        lunch = TimeWindow(
            start=DAY.replace(hour=12),
            end=DAY.replace(hour=13, minute=30),
        )
        utilities = {A1: 3.0, A2: 2.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            A2: AttractionStatus.OPERATING,
        }
        context = _live_context(waits={A1: 10.0, A2: 5.0}, statuses=statuses)

        plan = optimizer.build_plan(
            constraints=_constraints(lunch_window=lunch),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
            restaurant_node_ids=[RESTAURANT_1],
        )

        meal_stops = [s for s in plan.stops if s.kind == StopKind.MEAL]
        assert len(meal_stops) == 1
        assert meal_stops[0].node_id == RESTAURANT_1


class TestRestFrequency:
    """C19: rest stops inserted based on rest_frequency_minutes."""

    def test_rest_inserted_before_threshold(self) -> None:
        """When active time approaches rest_frequency, a REST stop is inserted."""
        optimizer = _build_optimizer()
        # rest_frequency = 60 min; each attraction takes ~30 min
        # (walk 5 + wait 10 + dur 15 = 30 min per stop)
        # After 2 stops (~60 min), a rest should be inserted before the 3rd
        reqs = [
            AccessibilityRequirements(
                guest_id="g1",
                rest_frequency_minutes=60,
                consent=True,
            ),
        ]
        utilities = {A1: 3.0, A2: 2.0, A3: 1.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            A2: AttractionStatus.OPERATING,
            A3: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 10.0, A2: 10.0, A3: 10.0},
            statuses=statuses,
        )

        plan = optimizer.build_plan(
            constraints=_constraints(),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
            accessibility_reqs=reqs,
        )

        rest_stops = [s for s in plan.stops if s.kind == StopKind.REST]
        assert len(rest_stops) >= 1, (
            "Expected at least one REST stop due to rest_frequency_minutes=60"
        )

    def test_no_rest_without_accessibility_requirement(self) -> None:
        """No REST stops are inserted when no guest has rest_frequency."""
        optimizer = _build_optimizer()
        utilities = {A1: 3.0, A2: 2.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            A2: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 10.0, A2: 5.0},
            statuses=statuses,
        )

        plan = optimizer.build_plan(
            constraints=_constraints(),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
        )

        rest_stops = [s for s in plan.stops if s.kind == StopKind.REST]
        assert len(rest_stops) == 0


class TestDepartureTimeCutoff:
    """No stop extends beyond departure_time."""

    def test_all_stops_end_before_departure(self) -> None:
        optimizer = _build_optimizer()
        utilities = {A1: 3.0, A2: 2.0, A3: 1.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            A2: AttractionStatus.OPERATING,
            A3: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 10.0, A2: 5.0, A3: 15.0},
            statuses=statuses,
        )
        departure = 14  # early departure

        plan = optimizer.build_plan(
            constraints=_constraints(departure_hour=departure),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
        )

        limit = DAY.replace(hour=departure)
        for s in plan.stops:
            assert s.departure_time <= limit, (
                f"Stop {s.node_id} departs at {s.departure_time}, "
                f"past the {limit} limit"
            )


class TestSatisfactionNormalization:
    """C20: per-guest satisfaction normalized by eligible set."""

    def test_satisfaction_with_group_objective(self) -> None:
        """When a GroupObjective with per_guest_eligible is given, satisfaction
        is normalized by the eligible set size."""
        optimizer = _build_optimizer()
        utilities = {A1: 3.0, A2: 2.0, A3: 1.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            A2: AttractionStatus.OPERATING,
            A3: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 10.0, A2: 5.0, A3: 10.0},
            statuses=statuses,
        )
        guests = _guests("adult", "child")

        group_obj = GroupObjective(
            objective_version="v1",
            weights={"queue": 0.5},
            per_guest_eligible={
                "adult": [A1, A2, A3],  # eligible for all 3
                "child": [A2],  # eligible for 1 only (height restriction)
            },
            hard_constraints=HardConstraintSet(),
            fairness=FairnessConfig(lambda_fairness=0.1, min_satisfaction_floor=0.0),
            event_thresholds=EventThresholds(),
        )

        plan = optimizer.build_plan(
            constraints=_constraints(guests=guests),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
            group_objective=group_obj,
        )

        assert "adult" in plan.per_guest_satisfaction
        assert "child" in plan.per_guest_satisfaction
        # Child's satisfaction should be higher relative to their eligible set
        # (they are eligible for 1 attraction and it should be visited)
        assert 0.0 <= plan.per_guest_satisfaction["child"] <= 1.0
        assert 0.0 <= plan.per_guest_satisfaction["adult"] <= 1.0

    def test_satisfaction_fallback_without_group_objective(self) -> None:
        """Without GroupObjective, satisfaction falls back to a ratio of
        attractions served vs total attraction stops."""
        optimizer = _build_optimizer()
        utilities = {A1: 3.0}
        statuses = {A1: AttractionStatus.OPERATING}
        context = _live_context(waits={A1: 10.0}, statuses=statuses)

        plan = optimizer.build_plan(
            constraints=_constraints(),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
        )

        assert "g1" in plan.per_guest_satisfaction
        assert plan.per_guest_satisfaction["g1"] == pytest.approx(1.0)


class TestEmptyCandidateSet:
    """Edge case: no attractions available at all."""

    def test_empty_utilities_produces_empty_plan(self) -> None:
        optimizer = _build_optimizer()
        context = _live_context()

        plan = optimizer.build_plan(
            constraints=_constraints(),
            context=context,
            utilities={},
            park=_park(),
        )

        assert plan.stops == []
        assert plan.total_wait_minutes == 0.0
        assert plan.total_walking_minutes == 0.0
        assert plan.objective_value == 0.0


class TestShowAnchors:
    """Fixed show anchors are placed at their showtime."""

    def test_show_anchor_inserted_at_showtime(self) -> None:
        optimizer = _build_optimizer()
        show_time = DAY.replace(hour=11, minute=0)
        utilities = {A1: 3.0, SHOW_1: 2.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            SHOW_1: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 10.0},
            statuses=statuses,
            showtimes={SHOW_1: [show_time]},
        )

        plan = optimizer.build_plan(
            constraints=_constraints(must_do=[SHOW_1]),
            context=context,
            utilities=utilities,
            park=_park(),
            catalog=_catalog(),
        )

        show_stops = [s for s in plan.stops if s.kind == StopKind.SHOW]
        assert len(show_stops) == 1
        assert show_stops[0].node_id == SHOW_1
        assert show_stops[0].arrival_time >= show_time


class TestProvenanceStrategy:
    """The plan's provenance records the optimizer strategy."""

    def test_optimizer_strategy_is_greedy_insertion(self) -> None:
        optimizer = _build_optimizer()
        utilities = {A1: 3.0}
        statuses = {A1: AttractionStatus.OPERATING}
        context = _live_context(waits={A1: 10.0}, statuses=statuses)

        plan = optimizer.build_plan(
            constraints=_constraints(),
            context=context,
            utilities=utilities,
            park=_park(),
        )

        assert plan.provenance.optimizer_strategy == "greedy_insertion"


class TestResolveRestFrequency:
    """Unit tests for the static helper _resolve_effective_rest_frequency."""

    def test_returns_minimum(self) -> None:
        reqs = [
            AccessibilityRequirements(
                guest_id="g1", rest_frequency_minutes=90, consent=True,
            ),
            AccessibilityRequirements(
                guest_id="g2", rest_frequency_minutes=60, consent=True,
            ),
        ]
        result = GreedyInsertionOptimizer._resolve_effective_rest_frequency(reqs)
        assert result == 60

    def test_returns_none_when_empty(self) -> None:
        assert GreedyInsertionOptimizer._resolve_effective_rest_frequency([]) is None

    def test_returns_none_when_no_rest_freq_set(self) -> None:
        reqs = [
            AccessibilityRequirements(guest_id="g1", consent=True),
        ]
        assert GreedyInsertionOptimizer._resolve_effective_rest_frequency(reqs) is None


class TestCodeReviewRegressions:
    """Regression test suite for Code Review PR #67 (Bugs 1-4)."""

    def test_must_do_show_unreachable_recorded_in_unmet_must_do(self) -> None:
        """Bug #1: If a must-do show cannot be reached before departure_time,
        it must appear in plan.unmet_must_do rather than silently disappearing."""
        optimizer = _build_optimizer()
        show_time = DAY.replace(hour=21, minute=20)
        departure_time = DAY.replace(hour=21, minute=35)
        utilities = {A1: 100.0, SHOW_1: 1.0}
        statuses = {
            A1: AttractionStatus.OPERATING,
            SHOW_1: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 180.0},
            statuses=statuses,
            showtimes={SHOW_1: [show_time]},
        )
        constraints = PartyConstraints(
            party_size=1,
            guests=_guests("g1"),
            must_do=[SHOW_1],
            avoid=[],
            lunch_window=None,
            departure_time=departure_time,
            constraints_version=1,
        )
        park = _park(opening_hour=18, closing_hour=23)

        plan = optimizer.build_plan(
            constraints=constraints,
            context=context,
            utilities=utilities,
            park=park,
            catalog=_catalog(),
        )

        show_in_stops = any(s.node_id == SHOW_1 for s in plan.stops)
        assert not show_in_stops, "Show cannot fit before departure_time"
        assert SHOW_1 in plan.unmet_must_do, "Unreachable must-do show must be in unmet_must_do"

    def test_greedy_marginal_utility_cost_ratio_selection(self) -> None:
        """Bug #2: Optimizer selects candidates with higher utility/cost ratio
        rather than raw utility. 5 cheap attractions (util 8 each, cost 25)
        yield 40 total utility over 1 expensive attraction (util 10, cost 200)."""
        optimizer = _build_optimizer()
        park = _park(opening_hour=9, closing_hour=13)
        departure = DAY.replace(hour=12, minute=20)

        cheaps = [f"cheap_{i}" for i in range(5)]
        utilities = {"expensive": 10.0}
        waits = {"expensive": 180.0}
        statuses = {"expensive": AttractionStatus.OPERATING}
        for cid in cheaps:
            utilities[cid] = 8.0
            waits[cid] = 5.0
            statuses[cid] = AttractionStatus.OPERATING

        context = _live_context(waits=waits, statuses=statuses)
        constraints = PartyConstraints(
            party_size=1,
            guests=_guests("g1"),
            must_do=[],
            avoid=[],
            lunch_window=None,
            departure_time=departure,
            constraints_version=1,
        )

        plan = optimizer.build_plan(
            constraints=constraints,
            context=context,
            utilities=utilities,
            park=park,
        )

        scheduled_ids = [s.node_id for s in plan.stops if s.kind == StopKind.ATTRACTION]
        assert set(scheduled_ids) == set(cheaps)
        assert "expensive" not in scheduled_ids
        assert plan.objective_value == pytest.approx(40.0)

    def test_show_with_invalid_showtimes_never_becomes_attraction(self) -> None:
        """Bug #3: A show whose only showtimes fall outside the park hours
        must NOT fall through and be scheduled as a StopKind.ATTRACTION ride.
        If it was in must_do, it should be listed in unmet_must_do."""
        optimizer = _build_optimizer()
        park = _park(opening_hour=9, closing_hour=22)
        early_show_time = DAY.replace(hour=8, minute=0)

        statuses = {
            "show-morning": AttractionStatus.OPERATING,
            A1: AttractionStatus.OPERATING,
        }
        context = _live_context(
            waits={A1: 10.0},
            statuses=statuses,
            showtimes={"show-morning": [early_show_time]},
        )
        constraints = _constraints(must_do=["show-morning"])
        utilities = {"show-morning": 10.0, A1: 5.0}

        plan = optimizer.build_plan(
            constraints=constraints,
            context=context,
            utilities=utilities,
            park=park,
            catalog=_catalog(),
        )

        show_as_attraction = [
            s for s in plan.stops
            if s.node_id == "show-morning" and s.kind == StopKind.ATTRACTION
        ]
        assert len(show_as_attraction) == 0, "Show must never be scheduled as an ATTRACTION"
        assert "show-morning" in plan.unmet_must_do

    def test_meal_strictly_within_lunch_window(self) -> None:
        """Bug #4: When few attractions are available and cursor finishes early
        (e.g. 09:20), a MEAL stop must start within lunch_window [12:00, 13:30],
        advancing the cursor to window.start rather than scheduling at 09:25."""
        optimizer = _build_optimizer()
        lunch_window = TimeWindow(
            start=DAY.replace(hour=12, minute=0),
            end=DAY.replace(hour=13, minute=30),
        )
        park = _park(opening_hour=9, closing_hour=22)
        utilities = {A1: 3.0}
        statuses = {A1: AttractionStatus.OPERATING}
        context = _live_context(waits={A1: 0.0}, statuses=statuses)

        plan = optimizer.build_plan(
            constraints=_constraints(lunch_window=lunch_window),
            context=context,
            utilities=utilities,
            park=park,
            catalog=_catalog(),
            restaurant_node_ids=[RESTAURANT_1],
        )

        meal_stops = [s for s in plan.stops if s.kind == StopKind.MEAL]
        assert len(meal_stops) == 1
        meal = meal_stops[0]
        assert meal.arrival_time >= lunch_window.start, (
            f"Meal arrival {meal.arrival_time} must be >= {lunch_window.start}"
        )
        assert meal.arrival_time <= lunch_window.end, (
            f"Meal arrival {meal.arrival_time} must be <= {lunch_window.end}"
        )
        assert meal.arrival_time == lunch_window.start

