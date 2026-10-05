"""State transition invariants: an unapproved candidate_plan must never
become current_plan, and rejection must not silently drop the reason.
"""

from datetime import datetime

import factories
import pytest

from parkmind.core.contracts import PARK_TZ, PartyConstraints, RejectionReason
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import (
    approve_plan,
    create_candidate_plan,
    increment_iteration,
    propose_plan_change,
    reject_plan,
    set_constraints,
    set_guest_profiles,
    set_live_context,
)


def _empty_state() -> ParkMindState:
    return {"thread_id": "test", "messages": []}


def test_set_constraints():
    constraints = PartyConstraints(
        party_size=1,
        guests=[factories.guest()],
        departure_time=datetime(2026, 9, 16, 20, 0, tzinfo=PARK_TZ),
        constraints_version=1,
    )
    state = set_constraints(_empty_state(), constraints)
    assert state["constraints"] == constraints


def test_set_guest_profiles():
    profiles = [factories.guest_profile()]
    state = set_guest_profiles(_empty_state(), profiles)
    assert state["guest_profiles"] == profiles


def test_set_live_context():
    ctx = factories.live_context()
    state = set_live_context(_empty_state(), ctx)
    assert state["live_context"] == ctx


def test_create_candidate_plan_does_not_touch_current_plan():
    """A freshly created candidate must never appear as current_plan."""
    plan = factories.plan()
    state = create_candidate_plan(_empty_state(), plan)
    assert state["candidate_plan"] == plan
    assert state.get("current_plan") is None


def test_create_candidate_plan_rejects_overwrite():
    state = create_candidate_plan(_empty_state(), factories.plan())
    with pytest.raises(ValueError, match="already exists"):
        create_candidate_plan(state, factories.plan(plan_id="plan_2"))


def test_approve_plan_requires_candidate():
    with pytest.raises(ValueError, match="No candidate plan"):
        approve_plan(_empty_state())


def test_approve_plan_moves_candidate_to_current():
    plan = factories.plan()
    state = create_candidate_plan(_empty_state(), plan)
    state = approve_plan(state)
    assert state["current_plan"] == plan
    assert state["candidate_plan"] is None
    assert state["approval"] == "APPROVED"


def test_reject_plan_discards_candidate_without_activating_it():
    """The core invariant: an unapproved candidate can never become active."""
    plan = factories.plan()
    state = create_candidate_plan(_empty_state(), plan)
    state = reject_plan(state, RejectionReason.TOO_MUCH_WALKING)

    assert state["candidate_plan"] is None
    assert state.get("current_plan") is None
    assert state["approval"] == "REJECTED"
    assert state["rejection_reason"] == RejectionReason.TOO_MUCH_WALKING


def test_reject_plan_requires_candidate():
    with pytest.raises(ValueError, match="No candidate plan"):
        reject_plan(_empty_state(), RejectionReason.OTHER)


def test_propose_plan_change_sets_pending_approval():
    proposal = factories.proposal()
    state = propose_plan_change(_empty_state(), proposal)
    assert state["proposal"] == proposal
    assert state["approval"] == "PENDING"


def test_increment_iteration_starts_at_zero():
    state = increment_iteration(_empty_state())
    assert state["iteration"] == 1
    state = increment_iteration(state)
    assert state["iteration"] == 2
