"""
Unit tests for Replanner service (P0-23).
Ensures completed stops freeze, location fallback works, and PlanDiff generates correctly.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from parkmind.core.contracts import (
    PARK_TZ,
    LiveContext,
    PartyConstraints,
    PlanExecutionState,
    Provenance,
)
from parkmind.core.contracts.models import Plan, Stop, StopKind
from parkmind.services.planning.replanner import Replanner

DAY = datetime(2026, 10, 8, tzinfo=PARK_TZ)

# --- Helpers ---

def _stop(node_id: str, hour: int) -> Stop:
    t0 = DAY.replace(hour=hour, minute=0)
    return Stop(
        node_id=node_id,
        kind=StopKind.ATTRACTION,
        arrival_time=t0,
        departure_time=t0 + timedelta(minutes=30),
        expected_wait_minutes=10.0,
        walking_minutes=5.0,
        utility=5.0,
    )

def _plan(stops: list[Stop]) -> Plan:
    return Plan(
        plan_id="plan-original",
        version=1,
        stops=stops,
        total_wait_minutes=10.0 * len(stops),
        total_walking_minutes=5.0 * len(stops),
        objective_value=0.0,
        unmet_must_do=[],
        provenance=Provenance(
            snapshot_id="snap1", retrieved_at=DAY, forecast_strategy="none",
            optimizer_strategy="none", constraints_version=1, objective_version="1",
            preference_model_version="auto"
        )
    )

@pytest.fixture
def mock_resolve_loop():
    loop = MagicMock()
    # By default, mock a successful resolution returning a plan with one future stop
    success_result = MagicMock()
    success_result.valid = True
    success_result.plan = _plan([_stop("future_attraction", 14)])
    success_result.unmet_must_do = []
    success_result.fatal_error = None
    loop.resolve.return_value = success_result
    return loop

@pytest.fixture
def mock_park_graph():
    return MagicMock()

@pytest.fixture
def base_context():
    ctx = MagicMock(spec=LiveContext)
    ctx.snapshot_id = "snap-new"
    ctx.retrieved_at = DAY.replace(hour=13)
    return ctx

@pytest.fixture
def base_constraints():
    mock = MagicMock(spec=PartyConstraints)
    mock.must_do = []
    # Mocking model_copy to return itself with updated fields
    mock.model_copy.side_effect = lambda update: MagicMock(spec=PartyConstraints, must_do=update.get("must_do", []))
    return mock

# --- Tests ---

def test_replanner_preserves_completed_stops_and_generates_new_id(mock_resolve_loop, mock_park_graph, base_context, base_constraints):
    """DoD 1, 4 & 5: Freezes completed stops, generates new ID, and outputs valid PlanDiff."""
    replanner = Replanner(resolve_loop=mock_resolve_loop, park_graph=mock_park_graph)
    
    past_stop = _stop("past_attraction", 10)
    skipped_stop = _stop("skipped_attraction", 11)
    current_plan = _plan([past_stop, skipped_stop])
    
    execution_state = PlanExecutionState(
        plan_id=current_plan.plan_id,
        as_of=DAY.replace(hour=13),
        completed_stop_ids=["past_attraction"],
        current_location_node_id=None
    )

    result = replanner.replan(
        current_plan=current_plan,
        execution_state=execution_state,
        context=base_context,
        constraints=base_constraints,
        utilities={},
        park=MagicMock(),
        catalog=[]
    )

    assert result.valid is True
    # DoD 1: Past stop is preserved, new future stop is appended
    assert [s.node_id for s in result.plan.stops] == ["past_attraction", "future_attraction"]
    
    # DoD 4: New plan_id is generated
    assert result.plan.plan_id != current_plan.plan_id
    assert result.plan.plan_id.startswith("plan_")
    
    # DoD 5: PlanDiff exists
    assert result.diff is not None

def test_replanner_starts_at_explicit_current_location(mock_resolve_loop, mock_park_graph, base_context, base_constraints):
    """DoD 3: If execution state provides a location, use it for the remaining horizon."""
    replanner = Replanner(resolve_loop=mock_resolve_loop, park_graph=mock_park_graph)
    current_plan = _plan([])
    
    execution_state = PlanExecutionState(
        plan_id=current_plan.plan_id,
        as_of=DAY.replace(hour=13),
        completed_stop_ids=[],
        current_location_node_id="fantasyland_hub"
    )

    replanner.replan(
        current_plan=current_plan,
        execution_state=execution_state,
        context=base_context,
        constraints=base_constraints,
        utilities={},
        park=MagicMock(),
        catalog=[]
    )

    # Verify resolve_loop received the explicit location
    _, kwargs = mock_resolve_loop.resolve.call_args
    assert kwargs["start_location_node_id"] == "fantasyland_hub"

def test_replanner_falls_back_to_last_completed_stop_location(mock_resolve_loop, mock_park_graph, base_context, base_constraints):
    """DoD 3: If no explicit location is set, fallback to the last completed stop."""
    replanner = Replanner(resolve_loop=mock_resolve_loop, park_graph=mock_park_graph)
    
    stop_1 = _stop("frontierland_ride", 10)
    stop_2 = _stop("adventureland_ride", 11)
    current_plan = _plan([stop_1, stop_2])
    
    execution_state = PlanExecutionState(
        plan_id=current_plan.plan_id,
        as_of=DAY.replace(hour=13),
        completed_stop_ids=["frontierland_ride", "adventureland_ride"],
        current_location_node_id=None
    )

    replanner.replan(
        current_plan=current_plan,
        execution_state=execution_state,
        context=base_context,
        constraints=base_constraints,
        utilities={},
        park=MagicMock(),
        catalog=[]
    )

    # Verify resolve_loop fallback to the last completed stop node
    _, kwargs = mock_resolve_loop.resolve.call_args
    assert kwargs["start_location_node_id"] == "adventureland_ride"

def test_replanner_removes_completed_must_dos_from_future_constraints(mock_resolve_loop, mock_park_graph, base_context, base_constraints):
    """Ensure we don't force the optimizer to schedule a MUST_DO that was already completed."""
    replanner = Replanner(resolve_loop=mock_resolve_loop, park_graph=mock_park_graph)
    current_plan = _plan([_stop("must_do_ride", 10)])
    
    base_constraints.must_do = ["must_do_ride", "future_must_do_ride"]
    
    execution_state = PlanExecutionState(
        plan_id=current_plan.plan_id,
        as_of=DAY.replace(hour=13),
        completed_stop_ids=["must_do_ride"],
        current_location_node_id=None
    )

    replanner.replan(
        current_plan=current_plan,
        execution_state=execution_state,
        context=base_context,
        constraints=base_constraints,
        utilities={},
        park=MagicMock(),
        catalog=[]
    )

    _, kwargs = mock_resolve_loop.resolve.call_args
    adjusted_constraints = kwargs["constraints"]
    assert adjusted_constraints.must_do == ["future_must_do_ride"]

def test_replanner_fails_closed_safely_if_future_is_infeasible(mock_resolve_loop, mock_park_graph, base_context, base_constraints):
    """DoD 2 (Fail Closed): If an attraction closes and leaves the plan infeasible, return valid=False safely."""
    # Mock resolve loop failing
    fail_result = MagicMock()
    fail_result.valid = False
    fail_result.plan = None
    fail_result.unmet_must_do = ["some_ride"]
    fail_result.fatal_error = "Infeasible constraints"
    mock_resolve_loop.resolve.return_value = fail_result

    replanner = Replanner(resolve_loop=mock_resolve_loop, park_graph=mock_park_graph)
    current_plan = _plan([])
    
    execution_state = PlanExecutionState(
        plan_id=current_plan.plan_id,
        as_of=DAY.replace(hour=13),
        completed_stop_ids=[],
        current_location_node_id=None
    )

    result = replanner.replan(
        current_plan=current_plan,
        execution_state=execution_state,
        context=base_context,
        constraints=base_constraints,
        utilities={},
        park=MagicMock(),
        catalog=[]
    )

    assert result.valid is False
    assert result.plan is None
    assert result.diff is None
    assert result.unmet_must_do == ["some_ride"]
    assert result.fatal_error == "Infeasible constraints"