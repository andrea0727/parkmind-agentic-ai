"""
ParkMindState — LangGraph state schema for the orchestration graph.

Implements Architecture v2.2 §34. This is the shared, checkpointed memory
for the planning and replanning graphs.

Design principles:
- current_plan / candidate_plan / proposal / approval stay separate so an
  unapproved candidate can never become the active plan.
- accessibility_ref holds only guest ids: AccessibilityRequirements (the
  hard-constraint payload) is never checkpointed here [C19]. It is loaded
  per run from the session store, keyed by these ids.
- approval and event_confirmation reuse the same upper-case literals as
  Proposal.approval_status (ApprovalStatus) [C23].
"""

from typing import Annotated, Literal, TypedDict

from langgraph.graph import add_messages

from parkmind.core.contracts import (
    CheckResult,
    Event,
    GroupObjective,
    GuestProfile,
    LiveContext,
    PartyConstraints,
    Plan,
    PlanDiff,
    PlanExecutionState,
    Proposal,
    Provenance,
    RejectionReason,
)


class ParkMindState(TypedDict, total=False):
    """Shared state across all agents in the orchestration graph.

    All fields are optional (total=False) because they're populated gradually
    as the workflow progresses. Agents should check for None before use.
    """

    # Session & Orchestration
    thread_id: str
    messages: Annotated[list, add_messages]
    iteration: int

    # Guest & Group Model
    constraints: PartyConstraints | None
    constraints_valid: bool
    pending_hard_constraint_confirmation: list[str] | None

    guest_profiles: list[GuestProfile]
    accessibility_ref: list[str]  # guest ids only; never AccessibilityRequirements [C19]
    group_objective: GroupObjective | None

    # Live Context & Execution
    live_context: LiveContext | None
    execution_state: PlanExecutionState | None

    # Plan Lifecycle (current/candidate stay distinct; only APPROVE activates)
    current_plan: Plan | None
    candidate_plan: Plan | None

    # Events (monitor / user reports)
    events: list[Event]
    event_confirmation: Literal["PENDING", "CONFIRMED", "DISMISSED"] | None

    # Checking & Proposal
    check_result: CheckResult | None
    diff: PlanDiff | None
    proposal: Proposal | None

    # Human Decision
    approval: Literal["PENDING", "APPROVED", "REJECTED", "EDITED"] | None
    rejection_reason: RejectionReason | None

    # Provenance & Learning
    provenance: list[Provenance]
    preference_model_version: str | None
