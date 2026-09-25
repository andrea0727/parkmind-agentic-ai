"""
Plan Synthesis Agent.

Generates an initial plan based on:
- Guest preferences (resolved)
- Live weather (from weather adapter)
- Available attractions & wait times (from attractions adapter)
- Park operating hours

Simple algorithm:
1. Filter attractions by guest preferences
2. Sort by: guest affinity > wait time < availability
3. Build chronological schedule respecting constraints
4. Return as Plan version=1

Persists to database.
"""

from datetime import UTC, datetime
from uuid import uuid4

import psycopg

from parkmind.core.contracts import Plan, Provenance
from parkmind.graph.state import ParkMindState
from parkmind.services.clients.postgres import PostgresPlanRepository, connect
from parkmind.services.ports.errors import RepositoryError


async def synthesize_plan(state: ParkMindState) -> ParkMindState:
    """Generate initial plan from guest + weather + attractions.

    Returns a DRAFT plan (version 1) ready for validation.
    Fills only required fields; full algorithm comes in future sprints.

    Connects to:
    - Weather adapter: Real weather data
    - Attractions adapter: Real attraction data + wait times
    - Database: Persist to PostgreSQL
    """
    guest = state.get("guest")
    if not guest:
        raise ValueError("Guest must be set")
    if not state.get("weather"):
        raise ValueError("Weather must be fetched first")
    if not state.get("attractions"):
        raise ValueError("Attractions must be fetched first")

    # Create minimal valid Plan
    plan = Plan(
        plan_id=f"plan_{guest.guest_id}_{uuid4().hex[:8]}",
        version=1,
        stops=[],  # TODO: Build from attractions
        total_wait_minutes=0.0,  # TODO: Calculate
        total_walking_minutes=0.0,  # TODO: Calculate
        objective_value=0.0,  # TODO: Score by preferences + constraints
        per_guest_satisfaction={guest.guest_id: 0.0},  # TODO: Compute
        unmet_must_do=[],  # TODO: Validate constraints
        provenance=Provenance(
            reason="initial_planning",
            source="plan_synthesis_agent",
            created_at=datetime.now(UTC),
            snapshot_id="",
            retrieved_at=datetime.now(UTC),
            forecast_strategy="weather_adapter",
            optimizer_strategy="greedy_affinity_then_wait",
            constraints_version="1",
            objective_version="1",
            preference_model_version="1",
        ),
    )

    # Persist to database
    try:
        with connect() as conn:
            repo = PostgresPlanRepository(conn)
            repo.save(plan)
    except (psycopg.Error, RepositoryError) as e:
        print(f"Warning: Could not save plan to database: {e}")
        # Continue anyway; plan is in memory

    state["candidate_plan"] = plan
    return state
