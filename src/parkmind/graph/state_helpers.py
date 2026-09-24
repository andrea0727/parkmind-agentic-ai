"""
State helpers and utilities for orchestration.

Provides functions to safely update, validate, and transition state
across the workflow. All functions assume state is valid
(contracts enforce this at boundaries).
"""

from parkmind.core.contracts import Guest, GuestProfile, Plan, Proposal, WeatherHour
from parkmind.graph.state import ParkMindState


def set_guest(state: ParkMindState, guest: Guest) -> ParkMindState:
    """Set the guest for this session. Once set, immutable."""
    if state.get("guest") is not None:
        raise ValueError("Guest already set; cannot change mid-session")
    state["guest"] = guest
    return state


def set_guest_profiles(state: ParkMindState, profiles: list[GuestProfile]) -> ParkMindState:
    """Set guest profiles (preferences). Overrides previous."""
    state["guest_profiles"] = profiles
    return state


def set_weather(state: ParkMindState, weather: list[WeatherHour]) -> ParkMindState:
    """Set live weather context. Fetched by weather agent."""
    state["weather"] = weather
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
    state["approval"] = "approved"
    return state


def reject_plan(state: ParkMindState, reason: str = "") -> ParkMindState:
    """Reject the candidate plan, discard it."""
    if state.get("candidate_plan") is None:
        raise ValueError("No candidate plan to reject")
    state["candidate_plan"] = None
    state["approval"] = "rejected"
    state["rejection_reason"] = reason
    return state


def propose_plan_change(state: ParkMindState, proposal: Proposal) -> ParkMindState:
    """Create a proposal for plan change, trigger interrupt for human."""
    state["proposal"] = proposal
    state["approval"] = "pending"
    return state


def increment_iteration(state: ParkMindState) -> ParkMindState:
    """Increment workflow iteration counter."""
    state["iteration"] = state.get("iteration", 0) + 1
    return state
