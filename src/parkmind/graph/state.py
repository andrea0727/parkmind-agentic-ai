"""
ParkMindState — LangGraph state schema for the orchestration graph.

This is the shared memory for all agents. Fields track:
1. Session identity & history
2. Guest model (profile + accessibility)
3. Live context (weather, park status, wait times)
4. Plan lifecycle (DRAFT → APPROVED → ACTIVE)
5. Proposals & interrupts (for human-in-the-loop)
6. Execution & events (what happened during the plan)

Design principles:
- current_plan and candidate_plan are deliberately separate so a plan
  can never become active without explicit interrupt()/approval.
- AccessibilityRequirements is NOT stored here (never checkpointed
  in LangGraph, only in session store).
- All timestamps are timezone-aware (America/New_York).
- Reducer functions merge additions, never overwrite.
"""

from typing import Annotated, Literal, TypedDict

from langgraph.graph import add_messages

from parkmind.core.contracts import (
    AccessibilityCheck,
    ApprovalStatus,
    Attraction,
    BehaviorEntry,
    Event,
    Guest,
    GuestProfile,
    Plan,
    PlanDiff,
    PlanExecutionState,
    Proposal,
    WeatherHour,
)


class ParkMindState(TypedDict, total=False):
    """Shared state across all agents in the orchestration graph.

    All fields are optional (total=False) because they're populated gradually
    as the workflow progresses. Agents should check for None before use.
    """

    # Session & Orchestration
    thread_id: str
    iteration: int
    messages: Annotated[list, add_messages]

    # Guest Model (loaded at session start, never updated)
    guest: Guest | None
    guest_profiles: list[GuestProfile]
    resolved_preferences: dict | None

    # Live Context (fetched by specialist agents)
    weather: list[WeatherHour] | None
    attractions: list[Attraction] | None
    park_status: dict | None

    # Plan Lifecycle (DRAFT → APPROVED → ACTIVE)
    # These are mutually exclusive; proposal forces decision
    current_plan: Plan | None
    candidate_plan: Plan | None

    # Proposals & Human-in-the-Loop
    proposal: Proposal | None
    approval: Literal["pending", "approved", "rejected", "edited"] | None

    # Execution & Monitoring
    execution_state: PlanExecutionState | None
    events: list[Event]
    behavior_signals: list[BehaviorEntry]

    # Validation & Debugging
    check_results: list[AccessibilityCheck]
    plan_diff: PlanDiff | None
    rejection_reason: str | None
