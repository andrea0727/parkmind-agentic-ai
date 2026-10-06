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

from datetime import datetime, timedelta

import pytest

from parkmind.core.contracts import (
    PARK_TZ,
    AccessibilityRequirements,
    Attraction,
    AttractionCategory,
    AttractionStatus,
    CoverageReport,
    DataSource,
    EventThresholds,
    FairnessConfig,
    GroupObjective,
    Guest,
    GuestRole,
    HardConstraintSet,
    LiveContext,
    Park,
    PartyConstraints,
    Provenance,
    StopKind,
    TimeWindow,
    WaitEstimate,
)
from parkmind.services.clients.knowledge.in_memory import InMemoryKnowledgeStore
from parkmind.services.personalization.preference_scorer import (
    PREFERENCE_SCORER_VERSION,
    PreferenceScorer,
    per_guest_satisfaction,
)
from parkmind.services.planning.forecast_service import ForecastService
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import WaitForecast

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
    graph = ParkGraph.from_sources(
        routing=routing, park=_park(), attractions=_catalog()
    )
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
            s.expected_wait_minutes for s in plan.stops if s.kind == StopKind.ATTRACTION
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

        attraction_stops = [s for s in plan.stops if s.kind == StopKind.ATTRACTION]
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
                guest_id="g1",
                rest_frequency_minutes=90,
                consent=True,
            ),
            AccessibilityRequirements(
                guest_id="g2",
                rest_frequency_minutes=60,
                consent=True,
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
        assert SHOW_1 in plan.unmet_must_do, (
            "Unreachable must-do show must be in unmet_must_do"
        )

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
            s
            for s in plan.stops
            if s.node_id == "show-morning" and s.kind == StopKind.ATTRACTION
        ]
        assert len(show_as_attraction) == 0, (
            "Show must never be scheduled as an ATTRACTION"
        )
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


# ===================================================================
# Issue #75: forecast waits per stop
# ===================================================================
PLAN_NOW = DAY.replace(hour=9)


class _ByHour:
    """A forecast strategy: ``morning`` before 13:00, ``afternoon`` after; ``None`` for unknown rides."""

    def __init__(
        self,
        waits: dict[str, tuple[float, float]],
        *,
        name: str = "api_forecast",
        source: DataSource = DataSource.THEMEPARKS_WIKI,
    ) -> None:
        self.name = name
        self._waits = waits
        self._source = source

    def forecast(self, attraction_id: str, at: datetime, *, now: datetime) -> WaitForecast | None:
        if attraction_id not in self._waits:
            return None
        morning, afternoon = self._waits[attraction_id]
        wait = morning if at.hour < 13 else afternoon
        return WaitForecast(attraction_id, at, wait, self.name, self._source, "snap-test", now)


def _forecast(waits: dict[str, tuple[float, float]]) -> ForecastService:
    return ForecastService([_ByHour(waits)])


def _attraction_stops(plan) -> list:  # type: ignore[no-untyped-def]
    return [s for s in plan.stops if s.kind == StopKind.ATTRACTION]


class TestForecastWaits:
    """A stop is charged the wait expected when the party gets there."""

    @pytest.mark.parametrize(("opening_hour", "expected"), [(9, 10.0), (14, 60.0)])
    def test_stop_is_charged_the_forecast_for_its_arrival(self, opening_hour: int, expected: float) -> None:
        context = _live_context(waits={A1: 5.0}, statuses={A1: AttractionStatus.OPERATING})

        plan = _build_optimizer().build_plan(
            constraints=_constraints(),
            context=context,
            utilities={A1: 1.0},
            park=_park(opening_hour=opening_hour),
            catalog=_catalog(),
            forecast_service=_forecast({A1: (10.0, 60.0)}),
            now=PLAN_NOW,
        )

        [stop] = _attraction_stops(plan)
        assert stop.expected_wait_minutes == expected  # not the posted 5
        assert stop.departure_time - stop.arrival_time == timedelta(minutes=expected + 15)

    def test_candidates_are_ranked_by_their_forecast_not_the_posted_wait(self) -> None:
        context = _live_context(
            waits={A1: 5.0, A2: 60.0},
            statuses={A1: AttractionStatus.OPERATING, A2: AttractionStatus.OPERATING},
        )
        kwargs = {
            "constraints": _constraints(),
            "context": context,
            "utilities": {A1: 1.0, A2: 1.0},
            "park": _park(),
            "catalog": _catalog(),
        }

        posted = _build_optimizer().build_plan(**kwargs)
        forecast = _build_optimizer().build_plan(
            **kwargs, forecast_service=_forecast({A1: (60.0, 60.0), A2: (5.0, 5.0)}), now=PLAN_NOW
        )

        assert _attraction_stops(posted)[0].node_id == A1
        assert _attraction_stops(forecast)[0].node_id == A2

    def test_a_rest_that_moves_the_arrival_past_the_hour_is_charged_then(self) -> None:
        # Open 12:25. A1: walk 5, wait 0, ride 15 -> leaves 12:45 with 20 active minutes.
        # A2 at 12:50 would be a morning wait, but rest_frequency 25 forces a 20-minute
        # REST first, so the party reaches A2 at 13:10: the afternoon forecast applies.
        context = _live_context(
            waits={A1: 0.0, A2: 0.0},
            statuses={A1: AttractionStatus.OPERATING, A2: AttractionStatus.OPERATING},
        )

        plan = _build_optimizer().build_plan(
            constraints=_constraints(),
            context=context,
            utilities={A1: 2.0, A2: 1.0},
            accessibility_reqs=[
                AccessibilityRequirements(guest_id="g1", rest_frequency_minutes=25, consent=True)
            ],
            park=Park(
                park_id="park-1",
                name="Test Park",
                opening_time=DAY.replace(hour=12, minute=25),
                closing_time=DAY.replace(hour=22),
            ),
            catalog=_catalog(),
            forecast_service=_forecast({A1: (0.0, 0.0), A2: (10.0, 60.0)}),
            now=PLAN_NOW,
        )

        kinds = [(s.kind, s.node_id) for s in plan.stops]
        assert kinds[:3] == [(StopKind.ATTRACTION, A1), (StopKind.REST, A1), (StopKind.ATTRACTION, A2)]
        a2 = plan.stops[2]
        assert a2.arrival_time == DAY.replace(hour=13, minute=10)
        assert a2.expected_wait_minutes == 60.0

    def test_same_inputs_and_forecasts_give_the_same_stops(self) -> None:
        context = _live_context(
            waits={A1: 5.0, A2: 30.0, A3: 20.0}, statuses=_default_statuses()
        )

        def run() -> list:
            return _build_optimizer().build_plan(
                constraints=_constraints(),
                context=context,
                utilities={A1: 3.0, A2: 2.0, A3: 1.0},
                park=_park(),
                catalog=_catalog(),
                forecast_service=_forecast({A1: (10.0, 60.0), A2: (20.0, 5.0)}),
                now=PLAN_NOW,
            ).stops

        assert run() == run()


class TestNoReading:
    """No forecast reading: the curated typical wait, never 0; nothing at all: not scheduled."""

    def test_no_reading_is_charged_the_typical_wait_not_zero(self) -> None:
        context = _live_context(statuses={A2: AttractionStatus.OPERATING})  # no posted wait either

        plan = _build_optimizer().build_plan(
            constraints=_constraints(),
            context=context,
            utilities={A2: 1.0},
            park=_park(),
            catalog=_catalog(),
            forecast_service=_forecast({}),
            now=PLAN_NOW,
        )

        [stop] = _attraction_stops(plan)
        assert stop.expected_wait_minutes == 15.0  # Ride Beta's typical_wait_minutes

    @pytest.mark.parametrize("must_do", [False, True])
    def test_no_reading_and_no_catalog_entry_is_not_scheduled(self, must_do: bool) -> None:
        ghost = "ghost-ride"
        context = _live_context(waits={ghost: 5.0}, statuses={ghost: AttractionStatus.OPERATING})

        plan = _build_optimizer().build_plan(
            constraints=_constraints(must_do=[ghost] if must_do else None),
            context=context,
            utilities={ghost: 1.0},
            park=_park(),
            catalog=_catalog(),
            forecast_service=_forecast({}),
            now=PLAN_NOW,
        )

        assert _attraction_stops(plan) == []
        assert plan.unmet_must_do == ([ghost] if must_do else [])


class TestForecastProvenance:
    """The plan says where every wait it charged came from."""

    def _plan(self, *, provenance=None):  # type: ignore[no-untyped-def]
        context = _live_context(
            statuses={A1: AttractionStatus.OPERATING, A2: AttractionStatus.OPERATING, A3: AttractionStatus.OPERATING}
        )
        service = ForecastService([
            _ByHour({A1: (10.0, 10.0)}, name="api_forecast", source=DataSource.THEMEPARKS_WIKI),
            _ByHour({A2: (10.0, 10.0)}, name="cached_snapshot", source=DataSource.CACHE),
        ])  # A3 has no reading anywhere: typical wait
        return _build_optimizer().build_plan(
            constraints=_constraints(),
            context=context,
            utilities={A1: 1.0, A2: 1.0, A3: 1.0},
            park=_park(),
            catalog=_catalog(),
            provenance=provenance,
            forecast_service=service,
            now=PLAN_NOW,
        )

    def test_label_and_sources_come_from_the_waits_actually_charged(self) -> None:
        plan = self._plan()

        assert {s.node_id for s in _attraction_stops(plan)} == {A1, A2, A3}
        assert plan.provenance.forecast_strategy == "api_forecast+cached_snapshot+typical_wait"
        assert plan.provenance.data_sources == [DataSource.CACHE, DataSource.THEMEPARKS_WIKI]

    def test_a_caller_provenance_keeps_its_other_fields(self) -> None:
        caller = Provenance(
            snapshot_id="snap-caller",
            retrieved_at=DAY.replace(hour=8),
            data_sources=[DataSource.OPEN_METEO],
            forecast_strategy="unset",
            optimizer_strategy="greedy_insertion",
            constraints_version=7,
            objective_version="obj-7",
            preference_model_version="pm-7",
        )

        recorded = self._plan(provenance=caller).provenance

        assert recorded.forecast_strategy == "api_forecast+cached_snapshot+typical_wait"
        assert recorded.data_sources == [DataSource.CACHE, DataSource.OPEN_METEO, DataSource.THEMEPARKS_WIKI]
        assert recorded.model_dump(exclude={"forecast_strategy", "data_sources"}) == caller.model_dump(
            exclude={"forecast_strategy", "data_sources"}
        )

    def test_without_a_forecast_service_provenance_says_current_wait(self) -> None:
        context = _live_context(waits={A1: 5.0}, statuses={A1: AttractionStatus.OPERATING})

        plan = _build_optimizer().build_plan(
            constraints=_constraints(), context=context, utilities={A1: 1.0}, park=_park()
        )

        assert plan.provenance.forecast_strategy == "current_wait"
        assert plan.provenance.data_sources == []


class TestForecastOptIn:
    """Without a forecast service nothing changes; with one, ``now`` is required."""

    def test_forecast_service_requires_now(self) -> None:
        with pytest.raises(ValueError, match="now is required"):
            _build_optimizer().build_plan(
                constraints=_constraints(),
                context=_live_context(),
                utilities={},
                forecast_service=_forecast({}),
            )

    def test_without_a_forecast_service_the_posted_wait_is_charged(self) -> None:
        context = _live_context(waits={A1: 5.0}, statuses={A1: AttractionStatus.OPERATING})

        plan = _build_optimizer().build_plan(
            constraints=_constraints(), context=context, utilities={A1: 1.0}, park=_park(opening_hour=14)
        )

        assert _attraction_stops(plan)[0].expected_wait_minutes == 5.0


class TestScorerSatisfaction:
    """With scores, satisfaction and the model version come from the PreferenceScorer (#74)."""

    def _setup(self):  # type: ignore[no-untyped-def]
        eligible = {"adult": [A1, A2, A3], "child": [A1, A2]}  # the child is too short for A3
        objective = GroupObjective(
            objective_version="1",
            weights={},
            per_guest_eligible=eligible,
            hard_constraints=HardConstraintSet(),
            fairness=FairnessConfig(lambda_fairness=0.0, min_satisfaction_floor=0.0),
            event_thresholds=EventThresholds(),
        )
        statuses = {a: AttractionStatus.OPERATING for a in (A1, A2, A3)}
        context = _live_context(waits={A1: 10.0, A2: 10.0, A3: 10.0}, statuses=statuses)
        scores = PreferenceScorer().score(
            objective=objective,
            profiles=[],
            attractions=[a for a in _catalog() if a.node_id in (A1, A2, A3)],
            live_context=context,
            knowledge=InMemoryKnowledgeStore({}, corpus_version="test"),
        )
        constraints = _constraints(guests=_guests("adult", "child"), departure_hour=10)
        return objective, context, scores, constraints

    def test_satisfaction_and_version_come_from_the_scorer(self) -> None:
        objective, context, scores, constraints = self._setup()

        plan = _build_optimizer().build_plan(
            constraints=constraints,
            context=context,
            utilities=scores.utilities(),
            park=_park(),
            catalog=_catalog(),
            group_objective=objective,
            scores=scores,
        )

        assert plan.per_guest_satisfaction == per_guest_satisfaction(plan, scores)
        assert plan.provenance.preference_model_version == PREFERENCE_SCORER_VERSION

    def test_scores_for_a_different_party_are_refused(self) -> None:
        objective, context, scores, _ = self._setup()

        with pytest.raises(ValueError, match="different parties"):
            _build_optimizer().build_plan(
                constraints=_constraints(guests=_guests("adult", "child", "grandma")),
                context=context,
                utilities=scores.utilities(),
                park=_park(),
                catalog=_catalog(),
                group_objective=objective,
                scores=scores,
            )

    def test_without_scores_the_count_based_satisfaction_stays(self) -> None:
        objective, context, scores, constraints = self._setup()

        plan = _build_optimizer().build_plan(
            constraints=constraints,
            context=context,
            utilities=scores.utilities(),
            park=_park(),
            catalog=_catalog(),
            group_objective=objective,
        )

        rides = {s.node_id for s in plan.stops if s.kind == StopKind.ATTRACTION}
        assert plan.per_guest_satisfaction == {
            "adult": len(rides & {A1, A2, A3}) / 3,
            "child": len(rides & {A1, A2}) / 2,
        }
        assert plan.provenance.preference_model_version == "auto"


class _Intermittent:
    """Answers once per attraction, then has no reading: a source that drops between calls."""

    name = "api_forecast"

    def __init__(self, wait: float) -> None:
        self._wait = wait
        self.calls: list[tuple[str, datetime]] = []

    def forecast(self, attraction_id: str, at: datetime, *, now: datetime) -> WaitForecast | None:
        self.calls.append((attraction_id, at))
        if any(a == attraction_id for a, _ in self.calls[:-1]):
            return None
        return WaitForecast(attraction_id, at, self._wait, self.name, DataSource.THEMEPARKS_WIKI, "snap-test", now)


class TestForecastReadsOncePerArrival:
    """What a candidate is ranked on is exactly what it is charged (Andrea, #76)."""

    @pytest.mark.parametrize("node", [A1, "ghost-ride"], ids=["in catalog", "not in catalog"])
    def test_a_source_that_drops_between_calls_still_gives_a_coherent_plan(self, node: str) -> None:
        context = _live_context(statuses={node: AttractionStatus.OPERATING})
        strategy = _Intermittent(wait=12.0)

        plan = _build_optimizer().build_plan(
            constraints=_constraints(),
            context=context,
            utilities={node: 1.0},
            park=_park(),
            catalog=_catalog(),
            forecast_service=ForecastService([strategy]),
            now=PLAN_NOW,
        )

        [stop] = _attraction_stops(plan)
        assert (stop.node_id, stop.expected_wait_minutes) == (node, 12.0)  # the reading it was ranked on
        assert plan.provenance.forecast_strategy == "api_forecast"

    def test_each_attraction_and_arrival_is_asked_at_most_once(self) -> None:
        context = _live_context(statuses={a: AttractionStatus.OPERATING for a in (A1, A2, A3)})
        counter = _Intermittent(wait=10.0)
        counter.forecast = _counting(counter.calls)  # type: ignore[method-assign]

        _build_optimizer().build_plan(
            constraints=_constraints(),
            context=context,
            utilities={A1: 3.0, A2: 2.0, A3: 1.0},
            park=_park(),
            catalog=_catalog(),
            forecast_service=ForecastService([counter]),
            now=PLAN_NOW,
        )

        assert counter.calls
        assert len(counter.calls) == len(set(counter.calls))


def _counting(calls: list[tuple[str, datetime]]):  # type: ignore[no-untyped-def]
    def forecast(attraction_id: str, at: datetime, *, now: datetime) -> WaitForecast:
        calls.append((attraction_id, at))
        return WaitForecast(attraction_id, at, 10.0, "api_forecast", DataSource.THEMEPARKS_WIKI, "snap-test", now)

    return forecast


class TestForecastInputsAndEdges:
    def test_a_naive_now_is_refused_up_front(self) -> None:
        with pytest.raises(ValueError, match="now must be timezone-aware"):
            _build_optimizer().build_plan(
                constraints=_constraints(),
                context=_live_context(),
                utilities={},
                forecast_service=_forecast({}),
                now=PLAN_NOW.replace(tzinfo=None),
            )

    @pytest.mark.parametrize("caller", [False, True])
    def test_a_plan_without_rides_records_no_forecast(self, caller: bool) -> None:
        provenance = Provenance(
            snapshot_id="snap-caller",
            retrieved_at=DAY.replace(hour=8),
            forecast_strategy="api_forecast",
            optimizer_strategy="greedy_insertion",
            constraints_version=1,
            objective_version="1",
            preference_model_version="1",
        ) if caller else None

        plan = _build_optimizer().build_plan(
            constraints=_constraints(),
            context=_live_context(),
            utilities={},
            park=_park(),
            provenance=provenance,
            forecast_service=_forecast({}),
            now=PLAN_NOW,
        )

        assert _attraction_stops(plan) == []
        assert plan.provenance.forecast_strategy == "none"  # replaced: nothing was charged

    def test_a_typical_wait_of_zero_is_charged_not_treated_as_missing(self) -> None:
        catalog = [a.model_copy(update={"typical_wait_minutes": 0}) if a.node_id == A2 else a for a in _catalog()]
        context = _live_context(statuses={A2: AttractionStatus.OPERATING})

        plan = _build_optimizer().build_plan(
            constraints=_constraints(),
            context=context,
            utilities={A2: 1.0},
            park=_park(),
            catalog=catalog,
            forecast_service=_forecast({}),
            now=PLAN_NOW,
        )

        [stop] = _attraction_stops(plan)
        assert stop.expected_wait_minutes == 0.0
        assert plan.provenance.forecast_strategy == "typical_wait"

