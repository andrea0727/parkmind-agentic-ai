"""
Guest Profile Resolver Agent.

Loads each party guest's stored preference profile and produces a
schema-valid GroupObjective placeholder (simple weighted average across
the party).

The real fairness-aware group resolution — per-guest eligible sets from
LiveContext.accessibility_results, hard-constraint union, event thresholds
from the most-sensitive guest — is services/personalization's
GroupPreferenceResolver [P0-16]. This agent only fills group_objective so
downstream nodes have a valid value until P0-16 lands.

Inputs: constraints (from state)
Outputs: guest_profiles (list[GuestProfile]), group_objective (GroupObjective)
"""

from parkmind.core.contracts import (
    EventThresholds,
    FairnessConfig,
    GroupObjective,
    HardConstraintSet,
)
from parkmind.graph.state import ParkMindState
from parkmind.services.use_cases.load_guest_profiles import LoadGuestProfilesUseCase


async def resolve_guest_preferences(state: ParkMindState) -> ParkMindState:
    """Load profiles for every guest in the party and compute a placeholder objective.

    Connects to PostgreSQL to fetch real GuestProfile records.
    Computes simple weighted average of preference values.
    """
    constraints = state.get("constraints")
    if not constraints:
        raise ValueError("Constraints must be set before resolving preferences")

    profiles = []
    for guest in constraints.guests:
        profiles.extend(LoadGuestProfilesUseCase().execute(guest.guest_id))

    # Placeholder weights (weighted average for now)
    weights = {
        "queue_tolerance": 0.5,  # Medium queue tolerance (default)
        "walking_tolerance": 0.7,  # Good walker (default)
    }
    queue_vals = [p.queue_tolerance.value for p in profiles if p.queue_tolerance]
    walking_vals = [p.walking_tolerance.value for p in profiles if p.walking_tolerance]
    if queue_vals:
        weights["queue_tolerance"] = sum(queue_vals) / len(queue_vals)
    if walking_vals:
        weights["walking_tolerance"] = sum(walking_vals) / len(walking_vals)

    state["guest_profiles"] = profiles
    state["group_objective"] = GroupObjective(
        objective_version="0-placeholder",
        weights=weights,
        hard_constraints=HardConstraintSet(
            must_do=constraints.must_do,
            avoid=constraints.avoid,
            party_walking_budget_minutes=constraints.party_walking_budget_minutes,
        ),
        fairness=FairnessConfig(lambda_fairness=0.0, min_satisfaction_floor=0.0),
        event_thresholds=EventThresholds(),
    )
    return state
