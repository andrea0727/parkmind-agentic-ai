"""
End-to-end: user message → concierge → resolve preferences → build plan →
check → propose → interrupt → approve.

These tests validate the orchestration graph and state transitions.
Skipped until database repositories are available.
"""

import pytest

from parkmind.core.contracts import Guest
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import approve_plan, set_guest


def test_parkmin_state_schema_builds():
    """Validate ParkMindState TypedDict is valid."""
    state: ParkMindState = {
        "thread_id": "test_123",
        "iteration": 0,
        "messages": [],
    }
    assert state["thread_id"] == "test_123"


def test_set_guest_works():
    """Test state helper: set_guest."""
    state: ParkMindState = {"thread_id": "test", "messages": []}
    guest = Guest(guest_id="guest_1", role="adult", height_cm=180)

    state = set_guest(state, guest)
    assert state["guest"] == guest


def test_set_guest_immutable():
    """Once set, guest cannot be changed."""
    state: ParkMindState = {"thread_id": "test", "messages": []}
    guest1 = Guest(guest_id="guest_1", role="adult", height_cm=180)
    guest2 = Guest(guest_id="guest_2", role="adult", height_cm=170)

    state = set_guest(state, guest1)
    with pytest.raises(ValueError, match="already set"):
        set_guest(state, guest2)


def test_approve_plan_transitions_state():
    """Plan approval moves candidate → current."""
    state: ParkMindState = {"thread_id": "test", "messages": []}

    # Use a mock plan dict for testing state transitions
    mock_plan = {"plan_id": "plan_1", "status": "DRAFT"}  # type: ignore

    state["candidate_plan"] = mock_plan  # type: ignore
    state = approve_plan(state)

    assert state["current_plan"] == mock_plan
    assert state["candidate_plan"] is None
    assert state["approval"] == "approved"


@pytest.mark.skip(reason="Waiting for database repositories to be available")
def test_initial_planning_graph_builds():
    """Graph compiles and can be invoked."""
    from parkmind.graph.initial_planning_graph import graph

    assert graph is not None


@pytest.mark.skip(reason="Waiting for database repositories to be available")
def test_graph_produces_approved_plan():
    """Full workflow: guest → preferences → weather → plan → approved.

    Implement once database repositories and schema are ready.
    """

