"""
Plan Synthesis Agent.

Generates an initial plan based on:
- Guest preferences (resolved group objective)
- Live context (weather, attractions, waits)
- Park operating hours

Simple algorithm:
1. Filter attractions by guest preferences
2. Sort by: guest affinity > wait time < availability
3. Build chronological schedule respecting constraints
4. Return as Plan version=1

Produces a DRAFT candidate_plan only. It is not persisted here: an
unchecked, unapproved plan must never reach the repository — that
happens once ConstraintChecker [P0-20] and human approval have run
(see initial_planning_graph._interrupt_for_approval).
"""

from uuid import uuid4

from parkmind.agents.elicit_prompt import ELICIT_PROMPT_VERSION
from parkmind.core.contracts import Plan, Provenance
from parkmind.graph.state import ParkMindState


async def synthesize_plan(state: ParkMindState) -> ParkMindState:
    """Generate initial plan from constraints + live context.

    Returns a DRAFT plan (version 1) ready for validation.
    Fills only required fields; full algorithm comes in future sprints.
    """
    constraints = state.get("constraints")
    if not constraints:
        raise ValueError("Constraints must be set")
    live_context = state.get("live_context")
    if not live_context:
        raise ValueError("Live context must be fetched first")

    guest_ids = [guest.guest_id for guest in constraints.guests]
    group_objective = state.get("group_objective")
    objective_version = group_objective.objective_version if group_objective else "0-placeholder"

    # Create minimal valid Plan
    plan = Plan(
        plan_id=f"plan_{uuid4().hex[:8]}",
        version=1,
        stops=[],  # TODO: Build from attractions
        total_wait_minutes=0.0,  # TODO: Calculate
        total_walking_minutes=0.0,  # TODO: Calculate
        objective_value=0.0,  # TODO: Score by preferences + constraints
        per_guest_satisfaction=dict.fromkeys(guest_ids, 0.0),  # TODO: Compute
        unmet_must_do=[],  # TODO: Validate constraints
        provenance=Provenance(
            snapshot_id=live_context.snapshot_id,
            retrieved_at=live_context.retrieved_at,
            forecast_strategy="weather_adapter",
            optimizer_strategy="greedy_affinity_then_wait",
            constraints_version=constraints.constraints_version,
            objective_version=objective_version,
            preference_model_version=state.get("preference_model_version") or "0-placeholder",
            prompt_versions={"elicit": ELICIT_PROMPT_VERSION},
        ),
    )

    state["candidate_plan"] = plan
    return state
