"""Unit tests for PlanDiff (P0-22 / #36).

Coverage:
  - Empty plans produce empty diff.
  - Identical plans (with and without REST/duplicate nodes) produce empty diff.
  - Added and removed attractions correctly identified.
  - Added and removed REST / non-attraction stops identified without node collisions.
  - Single-element movement in sequence only marks the moved stop in stops_reordered (LCS/LIS).
  - Reordering with realistic time shifts distinguishes order changes from modifications.
  - Attribute modifications and time shifts stored as JSON-native lists [old, new].
  - Pydantic / JSON serialization round-trip preservation (Postgres/SessionStore safety).
  - Determinism across multiple executions.
  - Integration with optimizer-generated plans.
"""

from __future__ import annotations

from datetime import datetime, timedelta

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
    Plan,
    PlanDiff,
    Provenance,
    Stop,
    StopKind,
    WaitEstimate,
)
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.planning.plan_diff import diff_plans

DAY = datetime(2026, 9, 16, tzinfo=PARK_TZ)


def _make_provenance() -> Provenance:
    return Provenance(
        snapshot_id="snap-1",
        retrieved_at=DAY.replace(hour=8),
        forecast_strategy="current_wait",
        optimizer_strategy="greedy_insertion",
        constraints_version=1,
        objective_version="auto",
        preference_model_version="auto",
    )


def _make_stop(
    node_id: str,
    arrival_hour: int = 9,
    arrival_min: int = 0,
    duration_min: int = 30,
    expected_wait: float = 15.0,
    walking: float = 5.0,
    utility: float = 1.0,
    served_guests: list[str] | None = None,
    kind: StopKind = StopKind.ATTRACTION,
) -> Stop:
    arrival = DAY.replace(hour=arrival_hour, minute=arrival_min)
    departure = arrival + timedelta(minutes=duration_min)
    return Stop(
        node_id=node_id,
        kind=kind,
        arrival_time=arrival,
        departure_time=departure,
        expected_wait_minutes=expected_wait,
        walking_minutes=walking,
        utility=utility,
        served_guests=served_guests or ["g1", "g2"],
    )


def _make_plan(stops: list[Stop], version: int = 1) -> Plan:
    return Plan(
        plan_id=f"plan-v{version}",
        version=version,
        stops=stops,
        total_wait_minutes=sum(s.expected_wait_minutes for s in stops),
        total_walking_minutes=sum(s.walking_minutes for s in stops),
        objective_value=sum(s.utility for s in stops),
        per_guest_satisfaction={"g1": 1.0, "g2": 1.0},
        provenance=_make_provenance(),
    )


class _FlatRoutingPort:
    def __init__(self, minutes: float = 5.0) -> None:
        self._minutes = minutes

    def walk_minutes(self, from_node: str, to_node: str) -> float:
        if from_node == to_node:
            return 0.0
        return self._minutes


class TestPlanDiff:
    """Test suite for diff_plans (Section 33)."""

    def test_empty_plans(self) -> None:
        p1 = _make_plan([], version=1)
        p2 = _make_plan([], version=2)
        diff = diff_plans(p1, p2)

        assert diff.stops_added == []
        assert diff.stops_removed == []
        assert diff.stops_reordered == []
        assert diff.stops_modified == {}

    def test_identical_plans(self) -> None:
        stops = [
            _make_stop("attraction-1", arrival_hour=9, arrival_min=0),
            _make_stop("attraction-2", arrival_hour=10, arrival_min=0),
        ]
        p1 = _make_plan(stops, version=1)
        p2 = _make_plan(stops, version=2)
        diff = diff_plans(p1, p2)

        assert diff.stops_added == []
        assert diff.stops_removed == []
        assert diff.stops_reordered == []
        assert diff.stops_modified == {}

    def test_identical_plans_with_rest_stops(self) -> None:
        """Optimizer places REST at cursor_node (same node_id as preceding attraction).

        Diffing identical plans containing repeated nodes must return an empty diff.
        """
        s_a1 = _make_stop("attraction-1", arrival_hour=9, arrival_min=0)
        s_a2 = _make_stop("attraction-2", arrival_hour=10, arrival_min=0)
        s_rest_a2 = _make_stop(
            "attraction-2",
            arrival_hour=10,
            arrival_min=30,
            duration_min=20,
            expected_wait=0.0,
            walking=0.0,
            utility=0.0,
            kind=StopKind.REST,
        )
        s_a3 = _make_stop("attraction-3", arrival_hour=11, arrival_min=0)

        plan = _make_plan([s_a1, s_a2, s_rest_a2, s_a3], version=1)
        diff = diff_plans(plan, plan)

        assert diff.stops_added == []
        assert diff.stops_removed == []
        assert diff.stops_reordered == []
        assert diff.stops_modified == {}

    def test_stops_added(self) -> None:
        s1 = _make_stop("attraction-1", arrival_hour=9)
        s2 = _make_stop("attraction-2", arrival_hour=10)
        s3 = _make_stop("attraction-3", arrival_hour=11)

        p1 = _make_plan([s1, s2], version=1)
        p2 = _make_plan([s1, s3, s2], version=2)

        diff = diff_plans(p1, p2)
        assert diff.stops_added == ["attraction-3"]
        assert diff.stops_removed == []
        assert diff.stops_reordered == []
        assert diff.stops_modified == {}

    def test_add_and_remove_rest_stop(self) -> None:
        """Adding or removing a REST stop at an existing attraction node must be
        reported as stops_added / stops_removed with kind qualifier without corrupting
        the attraction itself.
        """
        s_a1 = _make_stop("A", arrival_hour=9)
        s_rest_a = _make_stop("A", arrival_hour=9, arrival_min=30, kind=StopKind.REST)
        s_b = _make_stop("B", arrival_hour=10)

        plan_without_rest = _make_plan([s_a1, s_b], version=1)
        plan_with_rest = _make_plan([s_a1, s_rest_a, s_b], version=2)

        # 1. Add REST
        diff_add = diff_plans(plan_without_rest, plan_with_rest)
        assert diff_add.stops_added == ["A:REST"]
        assert diff_add.stops_removed == []
        assert diff_add.stops_modified == {}

        # 2. Remove REST
        diff_rem = diff_plans(plan_with_rest, plan_without_rest)
        assert diff_rem.stops_removed == ["A:REST"]
        assert diff_rem.stops_added == []
        assert diff_rem.stops_modified == {}

    def test_stops_removed(self) -> None:
        s1 = _make_stop("attraction-1", arrival_hour=9)
        s2 = _make_stop("attraction-2", arrival_hour=10)
        s3 = _make_stop("attraction-3", arrival_hour=11)

        p1 = _make_plan([s1, s2, s3], version=1)
        p2 = _make_plan([s1, s3], version=2)

        diff = diff_plans(p1, p2)
        assert diff.stops_added == []
        assert diff.stops_removed == ["attraction-2"]
        assert diff.stops_reordered == []
        assert diff.stops_modified == {}

    def test_stops_reordered_single_element_moved(self) -> None:
        """Moving A from first to last in [A, B, C, D] -> [B, C, D, A] must only
        report ['A'] in stops_reordered.
        """
        sa = _make_stop("A", arrival_hour=9)
        sb = _make_stop("B", arrival_hour=10)
        sc = _make_stop("C", arrival_hour=11)
        sd = _make_stop("D", arrival_hour=12)

        sa_end = _make_stop("A", arrival_hour=13)

        p1 = _make_plan([sa, sb, sc, sd], version=1)
        p2 = _make_plan([sb, sc, sd, sa_end], version=2)

        diff = diff_plans(p1, p2)
        assert diff.stops_reordered == ["A"]
        assert diff.stops_added == []
        assert diff.stops_removed == []
        assert "A" in diff.stops_modified

    def test_stops_reordered_with_realistic_time_shifts(self) -> None:
        """Swapping [A1, A2, A3] -> [A2, A1, A3] with realistic schedule adjustments."""
        s1 = _make_stop("A1", arrival_hour=9, arrival_min=0)
        s2 = _make_stop("A2", arrival_hour=10, arrival_min=0)
        s3 = _make_stop("A3", arrival_hour=11, arrival_min=0)

        # In swapped plan: A2 is visited at 9:00, A1 at 10:00
        s2_new = _make_stop("A2", arrival_hour=9, arrival_min=0)
        s1_new = _make_stop("A1", arrival_hour=10, arrival_min=0)
        s3_same = _make_stop("A3", arrival_hour=11, arrival_min=0)

        p1 = _make_plan([s1, s2, s3], version=1)
        p2 = _make_plan([s2_new, s1_new, s3_same], version=2)

        diff = diff_plans(p1, p2)
        assert diff.stops_added == []
        assert diff.stops_removed == []
        assert len(diff.stops_reordered) >= 1
        assert "A1" in diff.stops_modified
        assert "A2" in diff.stops_modified
        assert diff.stops_modified["A1"]["arrival_time"] == [
            s1.arrival_time.isoformat(),
            s1_new.arrival_time.isoformat(),
        ]

    def test_time_shifted_only(self) -> None:
        s1_old = _make_stop("attraction-1", arrival_hour=9, arrival_min=0)
        s1_new = _make_stop("attraction-1", arrival_hour=9, arrival_min=30)

        p1 = _make_plan([s1_old], version=1)
        p2 = _make_plan([s1_new], version=2)

        diff = diff_plans(p1, p2)
        assert diff.stops_added == []
        assert diff.stops_removed == []
        assert diff.stops_reordered == []
        assert "attraction-1" in diff.stops_modified
        assert "arrival_time" in diff.stops_modified["attraction-1"]
        assert "departure_time" in diff.stops_modified["attraction-1"]
        assert diff.stops_modified["attraction-1"]["arrival_time"] == [
            s1_old.arrival_time.isoformat(),
            s1_new.arrival_time.isoformat(),
        ]

    def test_substitution_distinguishable_from_time_shift(self) -> None:
        s1 = _make_stop("attraction-1", arrival_hour=9)
        s2 = _make_stop("attraction-2", arrival_hour=9)

        p1 = _make_plan([s1], version=1)
        p2 = _make_plan([s2], version=2)

        diff = diff_plans(p1, p2)
        assert diff.stops_removed == ["attraction-1"]
        assert diff.stops_added == ["attraction-2"]
        assert diff.stops_modified == {}

    def test_attribute_modifications(self) -> None:
        s1_old = _make_stop(
            "attraction-1",
            expected_wait=10.0,
            walking=5.0,
            utility=1.0,
            served_guests=["g1"],
        )
        s1_new = _make_stop(
            "attraction-1",
            expected_wait=25.0,
            walking=8.0,
            utility=2.0,
            served_guests=["g1", "g2"],
        )

        p1 = _make_plan([s1_old], version=1)
        p2 = _make_plan([s1_new], version=2)

        diff = diff_plans(p1, p2)
        assert "attraction-1" in diff.stops_modified
        mod = diff.stops_modified["attraction-1"]
        assert mod["expected_wait_minutes"] == [10.0, 25.0]
        assert mod["walking_minutes"] == [5.0, 8.0]
        assert mod["utility"] == [1.0, 2.0]
        assert mod["served_guests"] == [["g1"], ["g1", "g2"]]

    def test_substantially_changed_plans(self) -> None:
        sa = _make_stop("A", arrival_hour=9)
        sb = _make_stop("B", arrival_hour=10)
        sc = _make_stop("C", arrival_hour=11)
        sd = _make_stop("D", arrival_hour=12)

        sd_new = _make_stop("D", arrival_hour=9)
        sc_new = _make_stop("C", arrival_hour=10, arrival_min=30)
        se_new = _make_stop("E", arrival_hour=11)
        sb_new = _make_stop("B", arrival_hour=12)

        p1 = _make_plan([sa, sb, sc, sd], version=1)
        p2 = _make_plan([sd_new, sc_new, se_new, sb_new], version=2)

        diff = diff_plans(p1, p2)

        assert diff.stops_added == ["E"]
        assert diff.stops_removed == ["A"]
        assert "C" in diff.stops_modified
        assert "arrival_time" in diff.stops_modified["C"]

    def test_diff_determinism(self) -> None:
        sa = _make_stop("A", arrival_hour=9)
        sb = _make_stop("B", arrival_hour=10)
        sc = _make_stop("C", arrival_hour=11)
        sd = _make_stop("D", arrival_hour=12)

        p1 = _make_plan([sa, sb, sc, sd], version=1)
        p2 = _make_plan([sd, sc, _make_stop("E", arrival_hour=13), sb], version=2)

        diff1 = diff_plans(p1, p2)
        diff2 = diff_plans(p1, p2)

        assert diff1.stops_added == diff2.stops_added
        assert diff1.stops_removed == diff2.stops_removed
        assert diff1.stops_reordered == diff2.stops_reordered
        assert diff1.stops_modified == diff2.stops_modified

    def test_plan_diff_json_roundtrip_persistence(self) -> None:
        """PlanDiff must survive model_dump(mode='json') -> model_validate round-trip
        without data type mutations (Postgres / SessionStore persistence safety).
        """
        s1 = _make_stop("A", arrival_hour=9)
        s2 = _make_stop("B", arrival_hour=10)
        s1_shifted = _make_stop("A", arrival_hour=9, arrival_min=15)
        s3 = _make_stop("C", arrival_hour=11)

        p1 = _make_plan([s1, s2], version=1)
        p2 = _make_plan([s1_shifted, s3], version=2)

        diff = diff_plans(p1, p2)
        serialized_json = diff.model_dump(mode="json")
        rebuilt = PlanDiff.model_validate(serialized_json)

        assert rebuilt == diff
        assert rebuilt.stops_added == ["C"]
        assert rebuilt.stops_removed == ["B"]
        assert "A" in rebuilt.stops_modified
        assert isinstance(rebuilt.stops_modified["A"]["arrival_time"], list)

    def test_diff_plans_from_optimizer_execution(self) -> None:
        """Integration test: compare plans produced by GreedyInsertionOptimizer."""
        routing = _FlatRoutingPort(minutes=5.0)
        park = Park(
            park_id="test-park",
            name="Test Park",
            timezone="America/New_York",
            opening_time=DAY.replace(hour=9, minute=0),
            closing_time=DAY.replace(hour=22, minute=0),
        )
        catalog = [
            Attraction(
                node_id="A1",
                name="Attraction 1",
                category=AttractionCategory.THRILL,
                typical_wait_minutes=20.0,
                duration_minutes=15.0,
                outdoor=False,
            ),
            Attraction(
                node_id="A2",
                name="Attraction 2",
                category=AttractionCategory.FAMILY,
                typical_wait_minutes=15.0,
                duration_minutes=15.0,
                outdoor=False,
            ),
            Attraction(
                node_id="A3",
                name="Attraction 3",
                category=AttractionCategory.FAMILY,
                typical_wait_minutes=10.0,
                duration_minutes=15.0,
                outdoor=False,
            ),
        ]
        graph = ParkGraph.from_sources(routing=routing, park=park, attractions=catalog)
        optimizer = GreedyInsertionOptimizer(park_graph=graph)

        def _guests(*ids: str) -> list[Guest]:
            return [
                Guest(guest_id=gid, role=GuestRole.ADULT, height_cm=175.0)
                for gid in ids
            ]

        def _constraints(must_do: list[str]) -> PartyConstraints:
            return PartyConstraints(
                party_size=1,
                guests=_guests("g1"),
                must_do=must_do,
                avoid=[],
                departure_time=DAY.replace(hour=22),
                constraints_version=1,
            )

        def _live_context(
            waits: dict[str, float],
            statuses: dict[str, AttractionStatus],
        ) -> LiveContext:
            wait_estimates = {
                nid: WaitEstimate(
                    attraction_id=nid,
                    wait_minutes=w,
                    status=statuses.get(nid, AttractionStatus.OPERATING),
                )
                for nid, w in waits.items()
            }
            return LiveContext(
                snapshot_id="snap-test",
                retrieved_at=DAY.replace(hour=9),
                waits=wait_estimates,
                statuses=statuses,
                coverage=CoverageReport(
                    required_attractions_covered=True,
                    required_shows_covered=True,
                    weather_covered=True,
                    accessibility_checks_complete=True,
                ),
            )

        plan1 = optimizer.build_plan(
            constraints=_constraints(must_do=["A1", "A2"]),
            context=_live_context(
                waits={"A1": 10.0, "A2": 15.0, "A3": 0.0},
                statuses={
                    "A1": AttractionStatus.OPERATING,
                    "A2": AttractionStatus.OPERATING,
                    "A3": AttractionStatus.DOWN,
                },
            ),
            utilities={"A1": 2.0, "A2": 1.0, "A3": 0.0},
            park=park,
            catalog=catalog,
        )

        plan2 = optimizer.build_plan(
            constraints=_constraints(must_do=["A1", "A3"]),
            context=_live_context(
                waits={"A1": 10.0, "A2": 0.0, "A3": 5.0},
                statuses={
                    "A1": AttractionStatus.OPERATING,
                    "A2": AttractionStatus.DOWN,
                    "A3": AttractionStatus.OPERATING,
                },
            ),
            utilities={"A1": 2.0, "A2": 0.0, "A3": 3.0},
            park=park,
            catalog=catalog,
        )

        diff = diff_plans(plan1, plan2)
        assert "A2" in diff.stops_removed
        assert "A3" in diff.stops_added
