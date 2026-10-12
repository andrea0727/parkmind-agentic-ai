"""
Integration tests for the Replanner (P0-23).
Exercises the real interaction between Replanner, PlannerResolveLoop,
GreedyInsertionOptimizer, and ConstraintChecker to guarantee Done-when criteria.
"""

from datetime import datetime, timedelta, timezone

import pytest

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    AttractionCategory,
    AttractionStatus,
    Guest,
    LiveContext,
    Park,
    PartyConstraints,
    Plan,
    PlanExecutionState,
    Provenance,
    Stop,
    StopKind,
)
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.replanner import Replanner
from parkmind.services.planning.resolve_loop import PlannerResolveLoop


class DummyParkGraph:
    """A simple graph to deterministically verify start_location_node_id routing."""
    def walk_minutes(self, origin: str, destination: str) -> float:
        if origin == destination:
            return 0.0
        # Specific route to test if the optimizer starts from the current location
        if origin == "attr_b" and destination == "attr_c":
            return 12.0
        return 5.0


@pytest.fixture
def real_replanner() -> Replanner:
    graph = DummyParkGraph()
    # Using the real components, no MagicMocks for the core logic
    optimizer = GreedyInsertionOptimizer(park_graph=graph)
    checker = ConstraintChecker()
    resolve_loop = PlannerResolveLoop(optimizer=optimizer, checker=checker)
    return Replanner(resolve_loop=resolve_loop, park_graph=graph) # type: ignore


def test_replanner_integration_done_when_criteria(real_replanner: Replanner) -> None:
    # 1. Setup Data
    tz = timezone.utc
    now = datetime(2026, 10, 11, 9, 0, tzinfo=tz)
    park = Park(park_id="dummy_park", name="Dummy Park", opening_time=now, closing_time=now + timedelta(hours=12))
    catalog = [
        Attraction(node_id="attr_a", name="A", typical_wait_minutes=10, category=AttractionCategory.FAMILY),
        Attraction(node_id="attr_b", name="B", typical_wait_minutes=10, category=AttractionCategory.FAMILY),
        Attraction(node_id="attr_c", name="C", typical_wait_minutes=10, category=AttractionCategory.FAMILY),
        Attraction(node_id="attr_d", name="D", typical_wait_minutes=10, category=AttractionCategory.FAMILY),
    ]
    
    guest = Guest(guest_id="g1", name="John", role="adult")
    constraints = PartyConstraints(
        party_size=1,
        guests=[guest], 
        must_do=["attr_a", "attr_b", "attr_c", "attr_d"], 
        departure_time=park.closing_time,
        constraints_version=1
    )
    utilities = {"attr_a": 10.0, "attr_b": 10.0, "attr_c": 10.0, "attr_d": 10.0}
    
    # 2. Setup Current Plan (as if planned at 09:00)
    old_stops = [
        Stop(node_id="attr_a", kind=StopKind.ATTRACTION, arrival_time=now, departure_time=now + timedelta(minutes=30), expected_wait_minutes=5, walking_minutes=5, utility=10, served_guests=["g1"]),
        Stop(node_id="attr_b", kind=StopKind.ATTRACTION, arrival_time=now + timedelta(minutes=35), departure_time=now + timedelta(minutes=65), expected_wait_minutes=5, walking_minutes=5, utility=10, served_guests=["g1"]),
    ]
    current_plan = Plan(
        plan_id="plan_old", version=1, stops=old_stops, total_wait_minutes=10, 
        total_walking_minutes=10, objective_value=20, per_guest_satisfaction={"g1": 1.0}, 
        unmet_must_do=[], 
        provenance=Provenance(snapshot_id="s1", retrieved_at=now, forecast_strategy="none", optimizer_strategy="greedy_insertion", constraints_version=1, objective_version="auto", preference_model_version="auto")
    )
    
    # 3. Setup Execution State & Live Context (Time is now 10:06)
    as_of = now + timedelta(hours=1, minutes=6) # 10:06
    execution_state = PlanExecutionState(
        plan_id="plan_old",
        as_of=as_of,
        completed_stop_ids=["attr_a", "attr_b"],
        current_location_node_id="attr_b",  # Guest is currently standing at B
        remaining_walking_cap_minutes={"g1": 30.0}, # Testing fatigue limits
    )
    
    # Attr D is suddenly closed (Testing closure -> gracefully ignored)
    context = LiveContext(
        snapshot_id="s2", retrieved_at=as_of,
        statuses={"attr_a": AttractionStatus.OPERATING, "attr_b": AttractionStatus.OPERATING, "attr_c": AttractionStatus.OPERATING, "attr_d": AttractionStatus.DOWN},
        waits={
            "attr_c": {
                "attraction_id": "attr_c", 
                "wait_minutes": 15, 
                "status": AttractionStatus.OPERATING
            }
        }, showtimes={}, weather=[], accessibility_results=[],
        coverage={
            "required_attractions_covered": True, 
            "required_shows_covered": True, 
            "weather_covered": True, 
            "accessibility_checks_complete": True
        }
    )
    
    reqs = [AccessibilityRequirements(guest_id="g1", daily_walking_limit_minutes=40, consent=True)]

    # 4. Execute Replanner
    result = real_replanner.replan(
        current_plan=current_plan,
        execution_state=execution_state,
        context=context,
        constraints=constraints,
        utilities=utilities,
        park=park,
        catalog=catalog,
        accessibility_reqs=reqs,
    )

    # 5. Assertions (Done-when criteria matching the reviewer's checklist)
    assert result.valid is True
    new_plan = result.plan
    assert new_plan is not None
    assert new_plan.plan_id != "plan_old" # P0-12 Immutable
    
    # A) Preserves completed stops exactly
    assert new_plan.stops[0].node_id == "attr_a"
    assert new_plan.stops[1].node_id == "attr_b"
    
    # B) No repeated stops
    stop_ids = [s.node_id for s in new_plan.stops]
    assert len(stop_ids) == len(set(stop_ids))
    
    # C) Future stops are scheduled AFTER as_of (10:06) and DO NOT include DOWN attractions
    future_stops = new_plan.stops[2:]
    for s in future_stops:
        assert s.arrival_time >= as_of
        assert s.node_id not in ["attr_a", "attr_b"]
        assert s.node_id != "attr_d"
        
    # D) First future stop walks from "attr_b" (12 minutes according to DummyParkGraph)
    assert future_stops[0].walking_minutes == 12.0
    
    # E) D is gracefully degraded to unmet_must_do (Fail-closed constraint)
    assert "attr_d" in result.unmet_must_do