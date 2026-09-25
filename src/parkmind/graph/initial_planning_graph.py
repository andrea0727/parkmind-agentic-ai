"""
Initial planning state machine.

Workflow: START → resolve_preferences → fetch_context → synthesize_plan →
          interrupt_approval → END

Exposes a module-level compiled `graph` for orchestration.py to use.
"""

from datetime import UTC, datetime

from langgraph.graph import END, START, StateGraph

from parkmind.agents.plan_synthesis_agent import synthesize_plan
from parkmind.agents.preference_resolver_agent import resolve_guest_preferences
from parkmind.graph.state import ParkMindState
from parkmind.services.clients import OpenMeteoClient, ThemeParksClient
from parkmind.services.clients.open_meteo_client import OpenMeteoClientError
from parkmind.services.clients.themeparks_client import ThemeParksClientError


def build_initial_planning_graph():
    """Build the initial planning orchestration graph.

    MVP version:
    - Load guest & preferences
    - Fetch weather & attractions
    - Generate plan
    - Interrupt for approval

    Future: Includes constraint validation, hallucination checks,
    and real-time replanning.
    """
    graph = StateGraph(ParkMindState)

    # Nodes
    graph.add_node("resolve_preferences", resolve_guest_preferences)
    graph.add_node("fetch_context", _fetch_context_from_apis)
    graph.add_node("synthesize_plan", synthesize_plan)
    graph.add_node("interrupt_approval", _interrupt_for_approval)

    # Edges
    graph.add_edge(START, "resolve_preferences")
    graph.add_edge("resolve_preferences", "fetch_context")
    graph.add_edge("fetch_context", "synthesize_plan")
    graph.add_edge("synthesize_plan", "interrupt_approval")
    graph.add_edge("interrupt_approval", END)

    return graph.compile()


async def _fetch_context_from_apis(state: ParkMindState) -> ParkMindState:
    """Fetch live weather & attractions from adapters."""
    guest = state.get("guest")
    if not guest:
        raise ValueError("Guest must be set before fetching context")

    # Fetch weather
    try:
        weather_client = OpenMeteoClient()
        today = datetime.now(UTC).date()
        weather = weather_client.get_hourly_forecast(
            latitude=28.3852,
            longitude=-81.5639,
            start_date=today,
            end_date=today,
        )
        state["weather"] = weather
    except OpenMeteoClientError as e:
        print(f"Warning: Could not fetch weather: {e}")
        state["weather"] = []

    # Fetch attractions
    try:
        parks_client = ThemeParksClient("80008297")
        attractions = parks_client.get_catalog()
        state["attractions"] = attractions
    except ThemeParksClientError as e:
        print(f"Warning: Could not fetch attractions: {e}")
        state["attractions"] = []

    return state


async def _interrupt_for_approval(state: ParkMindState) -> ParkMindState:
    """Interrupt workflow to wait for human approval of the plan.

    For now: auto-approve for testing.
    Future: Implement actual interrupt() call with replanning flow.
    """
    if not state.get("candidate_plan"):
        raise ValueError("No candidate plan to approve")

    state["approval"] = "approved"
    state["current_plan"] = state.get("candidate_plan")
    return state


# Compiled graph for use in orchestration
graph = build_initial_planning_graph()
