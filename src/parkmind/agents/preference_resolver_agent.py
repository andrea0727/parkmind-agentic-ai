"""
Guest Profile Resolver Agent.

Loads guest preferences from the database and resolves them into a
shared preference model for recommendation generation.

Inputs: guest (from state)
Outputs: resolved_preferences (dict), guest_profiles (list[GuestProfile])
"""

from parkmind.graph.state import ParkMindState
from parkmind.services.use_cases.load_guest_profiles import LoadGuestProfilesUseCase


async def resolve_guest_preferences(state: ParkMindState) -> ParkMindState:
    """Load guest profiles from database and compute resolved preferences.

    Connects to PostgreSQL to fetch real GuestProfile records.
    Computes simple weighted average of preference values.
    """
    guest = state.get("guest")
    if not guest:
        raise ValueError("Guest must be set before resolving preferences")

    guest_id = guest.guest_id

    # Fetch profiles from database (graceful degradation if DB unavailable)
    profiles = LoadGuestProfilesUseCase().execute(guest_id)

    # Compute resolved preferences (weighted average for now)
    resolved = {
        "queue_tolerance": 0.5,  # Medium queue tolerance (default)
        "walking_tolerance": 0.7,  # Good walker (default)
        "preferred_categories": [],  # Will fill from profiles
    }

    # Update with profile preferences if available
    if profiles:
        queue_vals = [p.queue_tolerance.value for p in profiles if p.queue_tolerance]
        walking_vals = [p.walking_tolerance.value for p in profiles if p.walking_tolerance]

        if queue_vals:
            resolved["queue_tolerance"] = sum(queue_vals) / len(queue_vals)
        if walking_vals:
            resolved["walking_tolerance"] = sum(walking_vals) / len(walking_vals)

    state["guest_profiles"] = profiles
    state["resolved_preferences"] = resolved
    return state
