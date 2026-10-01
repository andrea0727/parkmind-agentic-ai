"""Unit tests for PlanDiff (P0-22 / #36).

Coverage:
  - Empty plans
  - Identical plans (diff is empty)
  - Added stops (correctly identified in order)
  - Removed stops (correctly identified in order)
  - Reordered stops (moved stops identified without false positives from insertions/deletions)
  - Time-shifted stops (modified arrival/departure without substitution)
  - Other attribute modifications (wait, walk, utility, served_guests)
  - Attraction substitutions (distinguishable from time-only shifts)
  - Substantially changed plans (mixed changes)
  - Determinism across repeated executions
"""

from __future__ import annotations

from datetime import datetime, timedelta

from parkmind.core.contracts import (
    PARK_TZ,
    Plan,
    Provenance,
    Stop,
    StopKind,
)
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


class TestPlanDiff:
    """Test suite for diff_plans."""

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

    def test_stops_added(self) -> None:
        s1 = _make_stop("attraction-1", arrival_hour=9)
        s2 = _make_stop("attraction-2", arrival_hour=10)
        s3 = _make_stop("attraction-3", arrival_hour=11)

        p1 = _make_plan([s1, s2], version=1)
        p2 = _make_plan([s1, s3, s2], version=2)

        diff = diff_plans(p1, p2)
        assert diff.stops_added == ["attraction-3"]
        assert diff.stops_removed == []
        # Relative order of s1 and s2 did not change
        assert diff.stops_reordered == []
        assert diff.stops_modified == {}

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

    def test_stops_reordered(self) -> None:
        s1 = _make_stop("attraction-1", arrival_hour=9)
        s2 = _make_stop("attraction-2", arrival_hour=10)
        s3 = _make_stop("attraction-3", arrival_hour=11)

        # Invert s1 and s2 order
        p1 = _make_plan([s1, s2, s3], version=1)
        p2 = _make_plan([s2, s1, s3], version=2)

        diff = diff_plans(p1, p2)
        assert diff.stops_added == []
        assert diff.stops_removed == []
        assert diff.stops_reordered == ["attraction-2", "attraction-1"]
        assert diff.stops_modified == {}

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
        old_arr, new_arr = diff.stops_modified["attraction-1"]["arrival_time"]
        assert old_arr == s1_old.arrival_time
        assert new_arr == s1_new.arrival_time

    def test_substitution_distinguishable_from_time_shift(self) -> None:
        # Replacing attraction-1 with attraction-2 is an addition and removal,
        # not a modification
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
            "attraction-1", expected_wait=10.0, walking=5.0, utility=1.0, served_guests=["g1"]
        )
        s1_new = _make_stop(
            "attraction-1", expected_wait=25.0, walking=8.0, utility=2.0, served_guests=["g1", "g2"]
        )

        p1 = _make_plan([s1_old], version=1)
        p2 = _make_plan([s1_new], version=2)

        diff = diff_plans(p1, p2)
        assert "attraction-1" in diff.stops_modified
        mod = diff.stops_modified["attraction-1"]
        assert mod["expected_wait_minutes"] == (10.0, 25.0)
        assert mod["walking_minutes"] == (5.0, 8.0)
        assert mod["utility"] == (1.0, 2.0)
        assert mod["served_guests"] == (["g1"], ["g1", "g2"])

    def test_substantially_changed_plans(self) -> None:
        # Plan 1: [A, B, C, D]
        # Plan 2: [D, C, E, B] (A removed, E added, D moved to front, B moved to back, C modified time)
        sa = _make_stop("A", arrival_hour=9)
        sb = _make_stop("B", arrival_hour=10)
        sc = _make_stop("C", arrival_hour=11)
        sd = _make_stop("D", arrival_hour=12)

        sd_new = _make_stop("D", arrival_hour=9)
        sc_new = _make_stop("C", arrival_hour=10, arrival_min=30)  # shifted time
        se_new = _make_stop("E", arrival_hour=11)  # added
        sb_new = _make_stop("B", arrival_hour=12)

        p1 = _make_plan([sa, sb, sc, sd], version=1)
        p2 = _make_plan([sd_new, sc_new, se_new, sb_new], version=2)

        diff = diff_plans(p1, p2)

        assert diff.stops_added == ["E"]
        assert diff.stops_removed == ["A"]
        # Common nodes in p1: [B, C, D]
        # Common nodes in p2: [D, C, B] -> reordered
        assert "D" in diff.stops_reordered
        assert "B" in diff.stops_reordered
        # C was shifted in time
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