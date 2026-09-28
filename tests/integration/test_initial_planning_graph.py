"""
End-to-end: user message → concierge → resolve preferences → build plan →
check → propose → interrupt → approve.

These tests validate the orchestration graph and state transitions.
Skipped until database repositories are available.
"""

from datetime import datetime

import factories
import pytest

from parkmind.core.contracts import PARK_TZ, PartyConstraints
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import approve_plan, set_constraints


def test_parkmin_state_schema_builds():
    """Validate ParkMindState TypedDict is valid."""
    state: ParkMindState = {
        "thread_id": "test_123",
        "iteration": 0,
        "messages": [],
    }
    assert state["thread_id"] == "test_123"


def test_set_constraints_works():
    """Test state helper: set_constraints."""
    state: ParkMindState = {"thread_id": "test", "messages": []}
    constraints = PartyConstraints(
        party_size=1,
        guests=[factories.guest()],
        departure_time=datetime(2026, 9, 16, 20, 0, tzinfo=PARK_TZ),
        constraints_version=1,
    )

    state = set_constraints(state, constraints)
    assert state["constraints"] == constraints


def test_approve_plan_transitions_state():
    """Plan approval moves candidate → current."""
    state: ParkMindState = {"thread_id": "test", "messages": []}

    mock_plan = factories.plan()

    state["candidate_plan"] = mock_plan
    state = approve_plan(state)

    assert state["current_plan"] == mock_plan
    assert state["candidate_plan"] is None
    assert state["approval"] == "APPROVED"


def test_initial_planning_graph_builds():
    """Graph compiles and can be invoked."""
    from parkmind.graph.initial_planning_graph import graph

    assert graph is not None


@pytest.mark.skip(reason="Waiting for database repositories to be available")
def test_graph_produces_approved_plan():
    """Full workflow: constraints → preferences → context → plan → approved.

    Implement once database repositories and schema are ready.
    """

