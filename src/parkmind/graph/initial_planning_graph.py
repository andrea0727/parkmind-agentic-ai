"""
Initial planning graph (P0-30).

Workflow::

    ELICIT -> BUILD GUEST STATE -> CONFIRM HARD CONSTRAINTS -> VALIDATE      (elicitation graph)
      -> LOAD CONTEXT -> RESOLVE GROUP -> BUILD PLAN -> CHECK
           +-> EXPLAIN -> PROPOSE -> APPROVAL                                 (check passed)
           +-> END, nothing explained or proposed                             (check failed)

The elicitation graph (P0-29) is reused as is; the planning chain is its
``downstream`` stage, compiled as a subgraph that shares ``ParkMindState`` and the
parent's checkpointer. Context is loaded before the group is resolved [C23].

Ordering guarantees:

* ``propose`` is reachable only through the valid edge of ``check``, and
  ``_propose_plan`` re-asserts ``check_result.valid``: an unchecked or failing
  candidate is never proposed.
* ``explain`` runs after ``check`` and raises for an invalid result.
* The plan is activated only by ``_interrupt_for_approval`` on the resumed,
  human-driven path (invariant: the LLM never activates a plan).

Nodes call use cases in-process; the use cases take their ports from a
``DepsFactory`` (Postgres, the notice corpus and the snapshot collector by
default), so no MCP server is involved and the graph runs with it stopped.

Privacy [C19]: state holds only ``accessibility_ref`` (guest ids). Every node that
needs requirements reads them from the SessionStore through its use case, keyed by
``thread_id``. ``load_context`` is the first node to do so. All stores share the
one process-wide ``SESSION_MEMORY`` (``use_cases/session_memory.py``).

Resume values: ``confirm_hard_constraints`` and ``ask_missing`` as documented in
``elicitation_graph``; ``approval``: ``{"decision": "APPROVED" | "REJECTED",
"rejection_reason": <RejectionReason value>}`` (``EDITED``: P0-32).

Nothing before an ``interrupt()`` has a side effect: LangGraph re-runs a node from
the top on resume.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from parkmind.agents.elicit_agent import GuestInfoExtractor
from parkmind.core.contracts import PARK_TZ, ApprovalStatus, RejectionReason
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.elicitation_graph import (
    StateNode,
    build_elicitation_graph,
    ensure_confirmed,
)
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import approve_plan, propose_plan_change, reject_plan
from parkmind.services.use_cases.accessibility_intake import AccessibilityIntakeUseCase
from parkmind.services.use_cases.build_plan import BuildPlanUseCase
from parkmind.services.use_cases.check_plan import CheckPlanUseCase
from parkmind.services.use_cases.explain_plan import ExplainPlanUseCase
from parkmind.services.use_cases.load_context import LoadContextUseCase
from parkmind.services.use_cases.planning_deps import (
    MAGIC_KINGDOM_PARK_ID,
    DepsFactory,
    default_planning_deps,
    deferred_attraction_repository,
)
from parkmind.services.use_cases.propose_plan import ProposePlanUseCase
from parkmind.services.use_cases.resolve_attraction_names import AttractionNameResolver
from parkmind.services.use_cases.resolve_proposal import ResolveProposalUseCase

Clock = Callable[[], datetime]


def park_clock() -> datetime:
    """Timezone-aware 'now' in park time; core code only ever receives it injected."""
    return datetime.now(PARK_TZ)


class CheckNotPassedError(RuntimeError):
    """A plan reached a step that requires a passing ConstraintChecker result."""


def _require_valid_candidate(state: ParkMindState) -> None:
    check = state.get("check_result")
    if check is None or not check.valid:
        raise CheckNotPassedError("the candidate plan has not passed the constraint checker")


def _make_load_context_node(deps_factory: DepsFactory, clock: Clock) -> StateNode:
    use_case = LoadContextUseCase(deps_factory)

    def load_context(state: ParkMindState) -> dict[str, Any]:
        ensure_confirmed(state)
        loaded = use_case.execute(
            state["thread_id"], state.get("accessibility_ref") or [], clock()
        )
        return {"live_context": loaded.live_context}

    return load_context


def _make_resolve_group_node(deps_factory: DepsFactory) -> StateNode:
    use_case = BuildPlanUseCase(deps_factory)

    def resolve_group(state: ParkMindState) -> dict[str, Any]:
        ensure_confirmed(state)
        constraints = state["constraints"]
        live_context = state.get("live_context")
        if constraints is None or live_context is None:
            raise ValueError("constraints and live context must be loaded before resolving")
        objective = use_case.resolve_group(
            thread_id=state["thread_id"],
            constraints=constraints,
            profiles=state.get("guest_profiles") or [],
            accessibility_ref=state.get("accessibility_ref") or [],
            live_context=live_context,
        )
        return {"group_objective": objective}

    return resolve_group


def _make_build_plan_node(deps_factory: DepsFactory, clock: Clock) -> StateNode:
    use_case = BuildPlanUseCase(deps_factory)

    def build_plan(state: ParkMindState) -> dict[str, Any]:
        ensure_confirmed(state)
        constraints = state["constraints"]
        live_context = state.get("live_context")
        objective = state.get("group_objective")
        if constraints is None or live_context is None or objective is None:
            raise ValueError("constraints, live context and group objective are required")
        plan = use_case.build(
            thread_id=state["thread_id"],
            constraints=constraints,
            profiles=state.get("guest_profiles") or [],
            accessibility_ref=state.get("accessibility_ref") or [],
            live_context=live_context,
            objective=objective,
            now=clock(),
        )
        return {"candidate_plan": plan, "check_result": None, "explanation": None}

    return build_plan


def _make_check_plan_node(deps_factory: DepsFactory, clock: Clock) -> StateNode:
    use_case = CheckPlanUseCase(deps_factory)

    def check_plan(state: ParkMindState) -> dict[str, Any]:
        ensure_confirmed(state)
        plan = state.get("candidate_plan")
        constraints = state["constraints"]
        live_context = state.get("live_context")
        if plan is None or constraints is None or live_context is None:
            raise ValueError("a candidate plan, constraints and live context are required")
        result = use_case.execute(
            thread_id=state["thread_id"],
            plan=plan,
            constraints=constraints,
            accessibility_ref=state.get("accessibility_ref") or [],
            live_context=live_context,
            now=clock(),
        )
        return {"check_result": result}

    return check_plan


def _route_after_check(state: ParkMindState) -> str:
    check = state.get("check_result")
    return "explain" if check is not None and check.valid else END


def _make_explain_node(deps_factory: DepsFactory) -> StateNode:
    use_case = ExplainPlanUseCase(deps_factory)

    def explain(state: ParkMindState) -> dict[str, Any]:
        plan = state.get("candidate_plan")
        check = state.get("check_result")
        if plan is None or check is None:
            raise ValueError("a checked candidate plan is required")
        return {"explanation": use_case.execute(plan, check)}

    return explain


def _propose_plan(state: ParkMindState) -> ParkMindState:
    """Persist the candidate plan and open a PENDING proposal for human review.

    Runs once per candidate: this is the side effect that must happen before
    the interrupt, not inside the same node as interrupt() (see
    _interrupt_for_approval -- LangGraph re-executes a node from the top on
    every resume). Only a plan that passed the ConstraintChecker, with its
    explanation, may be proposed.
    """
    plan = state.get("candidate_plan")
    if not plan:
        raise ValueError("No candidate plan to propose")
    _require_valid_candidate(state)

    proposal = ProposePlanUseCase().execute(
        state["thread_id"],
        plan,
        proposal_id=f"prop_{uuid4().hex[:8]}",
        reason="Initial plan for the party",
        explanation=state.get("explanation") or "",
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


def build_planning_stage(
    deps_factory: DepsFactory = default_planning_deps, clock: Clock = park_clock
) -> Any:
    """LOAD CONTEXT -> RESOLVE GROUP -> BUILD PLAN -> CHECK -> EXPLAIN -> PROPOSE -> APPROVAL."""
    graph = StateGraph(ParkMindState)
    graph.add_node("load_context", _make_load_context_node(deps_factory, clock))
    graph.add_node("resolve_group", _make_resolve_group_node(deps_factory))
    graph.add_node("build_plan", _make_build_plan_node(deps_factory, clock))
    graph.add_node("check_plan", _make_check_plan_node(deps_factory, clock))
    graph.add_node("explain", _make_explain_node(deps_factory))
    graph.add_node("propose_plan", _propose_plan)
    graph.add_node("interrupt_approval", _interrupt_for_approval)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "resolve_group")
    graph.add_edge("resolve_group", "build_plan")
    graph.add_edge("build_plan", "check_plan")
    graph.add_conditional_edges("check_plan", _route_after_check, ["explain", END])
    graph.add_edge("explain", "propose_plan")
    graph.add_edge("propose_plan", "interrupt_approval")
    graph.add_edge("interrupt_approval", END)
    return graph.compile()


def build_initial_planning_graph(
    extractor: GuestInfoExtractor | None = None,
    intake: AccessibilityIntakeUseCase | None = None,
    checkpointer: Any = None,
    *,
    deps_factory: DepsFactory = default_planning_deps,
    clock: Clock = park_clock,
    names: AttractionNameResolver | None = None,
) -> Any:
    """Compile the end-to-end initial planning graph.

    Nothing is opened here: connections and provider clients are created per node
    by ``deps_factory``, so building the graph needs no database or network.
    """
    resolver = names or AttractionNameResolver(
        deferred_attraction_repository(deps_factory), MAGIC_KINGDOM_PARK_ID
    )
    return build_elicitation_graph(
        extractor,
        intake,
        checkpointer or default_checkpointer(),
        cast(StateNode, build_planning_stage(deps_factory, clock)),
        names=resolver,
    )
