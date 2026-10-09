"""
State helpers and utilities for orchestration.

Provides functions to safely update, validate, and transition state
across the workflow. All functions assume state is valid
(contracts enforce this at boundaries).
"""

from parkmind.core.contracts import (
    GuestProfile,
    LiveContext,
    PartyConstraints,
    Plan,
    Proposal,
    RejectionReason,
)
from parkmind.graph.state import ParkMindState


def set_constraints(
    state: ParkMindState, constraints: PartyConstraints
) -> ParkMindState:
    """Set party constraints for this session (may be replaced on ELICIT re-loop)."""
    state["constraints"] = constraints
    return state


def set_guest_profiles(
    state: ParkMindState, profiles: list[GuestProfile]
) -> ParkMindState:
    """Set guest profiles (preferences). Overrides previous."""
    state["guest_profiles"] = profiles
    return state


def set_live_context(state: ParkMindState, live_context: LiveContext) -> ParkMindState:
    """Set the live context snapshot. Fetched by the context loader."""
    state["live_context"] = live_context
    return state


def create_candidate_plan(state: ParkMindState, plan: Plan) -> ParkMindState:
    """Create a new candidate plan. Moves to state for approval."""
    if state.get("candidate_plan") is not None:
        raise ValueError("Candidate plan already exists; must approve or reject first")
    state["candidate_plan"] = plan
    return state


def approve_plan(state: ParkMindState) -> ParkMindState:
    """Approve the candidate plan, making it active."""
    if state.get("candidate_plan") is None:
        raise ValueError("No candidate plan to approve")
    state["current_plan"] = state["candidate_plan"]
    state["candidate_plan"] = None
    state["approval"] = "APPROVED"
    return state


def reject_plan(state: ParkMindState, reason: RejectionReason) -> ParkMindState:
    """Reject the candidate plan, discard it."""
    if state.get("candidate_plan") is None:
        raise ValueError("No candidate plan to reject")
    state["candidate_plan"] = None
    state["approval"] = "REJECTED"
    state["rejection_reason"] = reason
    return state


def propose_plan_change(state: ParkMindState, proposal: Proposal) -> ParkMindState:
    """Create a proposal for plan change, trigger interrupt for human."""
    state["proposal"] = proposal
    state["approval"] = "PENDING"
    return state


def increment_iteration(state: ParkMindState) -> ParkMindState:
    """Increment workflow iteration counter."""
    state["iteration"] = state.get("iteration", 0) + 1
    return state
