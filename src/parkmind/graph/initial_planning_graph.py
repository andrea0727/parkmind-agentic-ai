"""
Initial planning state machine.

Workflow: START → resolve_preferences → fetch_context → synthesize_plan →
          propose_plan → interrupt_approval → END

Exposes a module-level compiled `graph` for orchestration.py to use.

Deferred to P0-30 (load_context):
- Wiring a single process-scoped ``SessionMemory`` through every
  ``PostgresSessionStore`` the graph opens. Today no node in this graph
  reads accessibility requirements from the store, so there is nothing to
  share yet; the wiring lands with ``load_context``, which is where the
  first read happens. The #42 Done-when criterion "the graph connects
  exactly one process-scoped ``SessionMemory`` to each
  ``PostgresSessionStore``" therefore moves to P0-30. See the README
  section "Wiring the session store" for the required shape.
"""

from datetime import UTC, datetime
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from parkmind.agents.plan_synthesis_agent import synthesize_plan
from parkmind.agents.preference_resolver_agent import resolve_guest_preferences
from parkmind.core.contracts import (
    PARK_TZ,
    ApprovalStatus,
    CoverageReport,
    LiveContext,
    RejectionReason,
)
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import approve_plan, propose_plan_change, reject_plan
from parkmind.services.use_cases.collect_snapshot import snapshot_id_for
from parkmind.services.use_cases.load_live_context import LoadLiveContextUseCase
from parkmind.services.use_cases.propose_plan import ProposePlanUseCase
from parkmind.services.use_cases.resolve_proposal import ResolveProposalUseCase

# Magic Kingdom, Walt Disney World (themeparks.wiki entity id + coordinates).
_DEFAULT_PARK_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"
_DEFAULT_LATITUDE = 28.4177
_DEFAULT_LONGITUDE = -81.5812


def build_initial_planning_graph(checkpointer=None):
    """Build the initial planning orchestration graph.

    MVP version:
    - Resolve guest preferences
    - Fetch live context (weather & attractions)
    - Generate plan
    - Persist it and open a PENDING proposal
    - Interrupt for approval; resolve the proposal and activate on approval

    Future: Includes constraint validation (ConstraintChecker [P0-20]),
    hallucination checks, and real-time replanning.
    """
    graph = StateGraph(ParkMindState)

    # Nodes
    graph.add_node("resolve_preferences", resolve_guest_preferences)
    graph.add_node("fetch_context", _fetch_context_from_apis)
    graph.add_node("synthesize_plan", synthesize_plan)
    graph.add_node("propose_plan", _propose_plan)
    graph.add_node("interrupt_approval", _interrupt_for_approval)

    # Edges
    graph.add_edge(START, "resolve_preferences")
    graph.add_edge("resolve_preferences", "fetch_context")
    graph.add_edge("fetch_context", "synthesize_plan")
    graph.add_edge("synthesize_plan", "propose_plan")
    graph.add_edge("propose_plan", "interrupt_approval")
    graph.add_edge("interrupt_approval", END)

    return graph.compile(checkpointer=checkpointer or default_checkpointer())


async def _fetch_context_from_apis(state: ParkMindState) -> ParkMindState:
    """Fetch live weather & attractions and assemble a LiveContext snapshot."""
    constraints = state.get("constraints")
    if not constraints:
        raise ValueError("Constraints must be set before fetching context")

    use_case = LoadLiveContextUseCase(
        park_id=_DEFAULT_PARK_ID,
        latitude=_DEFAULT_LATITUDE,
        longitude=_DEFAULT_LONGITUDE,
    )
    now = datetime.now(PARK_TZ)
    today = now.date()
    weather = use_case.fetch_weather(start_date=today, end_date=today)
    attractions = use_case.fetch_attractions()

    # Honest coverage: only catalog + weather are available from this loader.
    # No live waits, statuses, showtimes, or accessibility checks yet [P0-23].
    coverage_gaps = []
    if not weather:
        coverage_gaps.append("weather")
    if not attractions:
        coverage_gaps.append("required_attractions")

    # Deterministic snapshot id keyed on (park, collection window) -- same
    # convention as SnapshotCollector [P0-11], so once P0-30 wires this node
    # to SnapshotRepository the id already matches an existing row and
    # provenance stops pointing at a phantom uuid.
    state["live_context"] = LiveContext(
        snapshot_id=snapshot_id_for(_DEFAULT_PARK_ID, now),
        retrieved_at=datetime.now(UTC),
        waits={},
        statuses={},
        showtimes={},
        weather=weather,
        accessibility_results=[],
        coverage=CoverageReport(
            required_attractions_covered=bool(attractions),
            required_shows_covered=False,
            weather_covered=bool(weather),
            accessibility_checks_complete=False,
            coverage_gaps=coverage_gaps,
        ),
        tool_trace=[],
    )

    return state


def _propose_plan(state: ParkMindState) -> ParkMindState:
    """Persist the candidate plan and open a PENDING proposal for human review.

    Runs once per candidate: this is the side effect that must happen before
    the interrupt, not inside the same node as interrupt() (see
    _interrupt_for_approval -- LangGraph re-executes a node from the top on
    every resume).

    TODO [P0-20]: gate this on ConstraintChecker passing once
    ``CheckPlanUseCase.execute`` is implemented (see
    ``services/use_cases/check_plan.py``, still ``NotImplementedError``).
    Today an unchecked candidate is still sent for approval.
    """
    plan = state.get("candidate_plan")
    if not plan:
        raise ValueError("No candidate plan to propose")

    proposal = ProposePlanUseCase().execute(
        state["thread_id"],
        plan,
        proposal_id=f"prop_{uuid4().hex[:8]}",
        reason="Initial plan for the party",
        explanation="First candidate generated from constraints and live context.",
    )
    return propose_plan_change(state, proposal)


def _interrupt_for_approval(state: ParkMindState) -> ParkMindState:
    """Pause for a human decision, then resolve the proposal accordingly.

    Nothing here before interrupt() may have a side effect: LangGraph
    re-executes this node from the top on resume. Activation only happens
    from this resumed, human-driven path -- never from an agent/LLM node
    (invariant: the LLM never activates a plan; see
    services/ports/plan_repository.py).

    Expects the resume value to be a mapping with a ``"decision"`` key set to
    an ApprovalStatus value (e.g. ``{"decision": "APPROVED"}``) and, for a
    rejection, an optional ``"rejection_reason"`` RejectionReason value.
    """
    plan = state.get("candidate_plan")
    proposal = state.get("proposal")
    if not plan or not proposal:
        raise ValueError("No candidate plan/proposal to approve")

    decision = interrupt({"candidate_plan": plan, "proposal": proposal})
    status = ApprovalStatus(decision["decision"])

    if status is ApprovalStatus.EDITED:
        raise NotImplementedError(
            "EDITED approval decisions are not supported yet (see P0-32)"
        )

    rejection_reason = (
        RejectionReason(decision["rejection_reason"])
        if decision.get("rejection_reason")
        else RejectionReason.OTHER
    )

    ResolveProposalUseCase().execute(
        state["thread_id"],
        proposal.proposal_id,
        status,
        at=datetime.now(UTC),
        rejection_reason=rejection_reason
        if status is not ApprovalStatus.APPROVED
        else None,
    )

    if status is ApprovalStatus.APPROVED:
        return approve_plan(state)
    return reject_plan(state, rejection_reason)


# Compiled graph for use in orchestration
graph = build_initial_planning_graph()
