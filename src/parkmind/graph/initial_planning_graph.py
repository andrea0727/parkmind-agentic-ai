"""
Initial planning graph (P0-30).

Workflow::

    ELICIT -> BUILD GUEST STATE -> CONFIRM HARD CONSTRAINTS -> VALIDATE      (elicitation graph)
      -> LOAD CONTEXT -> RESOLVE GROUP -> BUILD PLAN -> CHECK
           +-> EXPLAIN -> PROPOSE -> APPROVAL                                 (check passed)
           +-> NO VALID PLAN -> END, nothing explained or proposed            (check failed)

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
"rejection_reason": <RejectionReason value>}`` (``EDITED``: P0-32). The approval
interrupt payload carries ``"kind": "plan_approval"``; a resume value that does not
fit is answered with the same interrupt plus an ``"error"``, before any side effect.

A port that fails or has no data (``PlanningUnavailableError``) ends the planning
stage with a plain-language message and no plan, instead of an unhandled exception.

Nothing before an ``interrupt()`` has a side effect: LangGraph re-runs a node from
the top on resume.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from parkmind.agents.elicit_agent import GuestInfoExtractor
from parkmind.core.contracts import PARK_TZ, ApprovalStatus, RejectionReason, Plan
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
    PlanningUnavailableError,
    default_planning_deps,
    deferred_attraction_repository,
)
from parkmind.services.use_cases.propose_plan import ProposePlanUseCase
from parkmind.services.use_cases.resolve_attraction_names import AttractionNameResolver
from parkmind.services.use_cases.resolve_proposal import (
    ProposalNoLongerPendingError,
    ResolveProposalUseCase,
)

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

APPROVAL_INTERRUPT_KIND = "plan_approval"
_SUPERSEDED = (
    "Something in the park changed while you were deciding, so this proposal was "
    "set aside and nothing was activated."
)
_ASK_AGAIN = "Ask me for a new plan and I'll build it from the latest park information."
SUPERSEDED_MESSAGE = f"{_SUPERSEDED} {_ASK_AGAIN}"
SUPERSEDED_KEEPS_PLAN_MESSAGE = (
    f"{_SUPERSEDED} Your current plan stays as it was. {_ASK_AGAIN}"
)
UNAVAILABLE_MESSAGE = (
    "I couldn't put a plan together right now because some of the park information "
    "I need isn't available. Nothing was proposed. Please try again in a few minutes."
)


def park_clock() -> datetime:
    """Timezone-aware 'now' in park time; core code only ever receives it injected."""
    return datetime.now(PARK_TZ)


class CheckNotPassedError(RuntimeError):
    """A plan reached a step that requires a passing ConstraintChecker result."""


def _require_valid_candidate(state: ParkMindState) -> None:
    check = state.get("check_result")
    if check is None or not check.valid:
        raise CheckNotPassedError("the candidate plan has not passed the constraint checker")


def _guarded(node: StateNode, *, then: str | None) -> StateNode:
    """Turn a ``PlanningUnavailableError`` into a message and the end of the stage.

    A static edge would still fire after a ``Command(goto=END)``, so the node moves
    on to ``then`` itself (``None``: a conditional edge decides).
    """

    def guarded(state: ParkMindState) -> dict[str, Any] | Command[Any]:
        try:
            update = node(state)
        except PlanningUnavailableError:
            logger.exception("planning is unavailable")
            return Command(
                update={
                    "messages": [AIMessage(content=UNAVAILABLE_MESSAGE)],
                    "candidate_plan": None,
                    "check_result": None,
                    "explanation": None,
                },
                goto=END,
            )
        return update if then is None else Command(update=update, goto=then)

    return cast(StateNode, guarded)


def _make_load_context_node(deps_factory: DepsFactory, clock: Clock) -> StateNode:
    use_case = LoadContextUseCase(deps_factory)

    def load_context(state: ParkMindState) -> dict[str, Any]:
        ensure_confirmed(state)
        loaded = use_case.execute(
            state["thread_id"], state.get("accessibility_ref") or [], clock()
        )
        return {"live_context": loaded.for_state()}

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
        built = use_case.build(
            thread_id=state["thread_id"],
            constraints=constraints,
            profiles=state.get("guest_profiles") or [],
            accessibility_ref=state.get("accessibility_ref") or [],
            live_context=live_context,
            objective=objective,
            now=clock(),
        )
        return {
            "candidate_plan": built.plan,
            "live_context": built.live_context,
            "check_result": None,
            "explanation": None,
        }

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
    return "explain" if check is not None and check.valid else "no_valid_plan"


def _no_valid_plan(state: ParkMindState) -> dict[str, Any]:
    """Tell the user why nothing was proposed; ``check_result`` stays in state."""
    check = state.get("check_result")
    reasons = [v.message for v in check.violations] if check is not None else []
    lines = ["I couldn't build a plan that meets every requirement, so nothing was proposed."]
    lines += [f"- {reason}" for reason in reasons]
    lines.append("You can relax a constraint (for example the must-dos or the departure time) and I'll try again.")
    return {"messages": [AIMessage(content="\n".join(lines))], "candidate_plan": None}


def _make_explain_node(deps_factory: DepsFactory) -> StateNode:
    use_case = ExplainPlanUseCase(deps_factory)

    def explain(state: ParkMindState) -> dict[str, Any]:
        plan = state.get("candidate_plan")
        check = state.get("check_result")
        if plan is None or check is None:
            raise ValueError("a checked candidate plan is required")
        constraints = state.get("constraints")
        guests = constraints.guests if constraints is not None else []
        return {"explanation": use_case.execute(plan, check, guests=guests)}

    return explain


def _propose_plan(
    state: ParkMindState,
    *,
    deps_factory: DepsFactory = default_planning_deps,
    clock: Clock = park_clock,
) -> ParkMindState:
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

    proposal = ProposePlanUseCase(deps_factory).execute(
        state["thread_id"],
        plan,
        proposal_id=f"prop_{uuid4().hex[:8]}",
        reason="Initial plan for the party",
        explanation=state.get("explanation") or "",
        at=clock(),
    )
    return propose_plan_change(state, proposal)


_NOT_DECIDABLE = frozenset({ApprovalStatus.PENDING, ApprovalStatus.SUPERSEDED})


def _parse_approval(value: Any) -> tuple[ApprovalStatus, RejectionReason | None, Plan | dict[str, Any] | None] | str:
    """The decision and the rejection reason given, or why the resume value is not valid."""
    if not isinstance(value, Mapping):
        return "the decision must be a mapping with a 'decision' key"
    try:
        status = ApprovalStatus(value.get("decision"))
    except ValueError:
        return "'decision' must be APPROVED, REJECTED, or EDITED"
    
    if status in _NOT_DECIDABLE:
        return "'decision' must be APPROVED, REJECTED, or EDITED"
    
    raw_reason = value.get("rejection_reason")
    edited_plan = value.get("edited_plan")

    if status is ApprovalStatus.EDITED and not edited_plan:
        return "an 'edited_plan' must be provided when the decision is EDITED"

    if status is not ApprovalStatus.REJECTED and raw_reason:
        return "'rejection_reason' should only be provided when decision is REJECTED"

    if raw_reason in (None, ""):
        return status, None, edited_plan
    
    try:
        return status, RejectionReason(raw_reason), edited_plan
    except ValueError:
        return "'rejection_reason' is not a known reason"


def _interrupt_for_approval(
    state: ParkMindState,
    *,
    deps_factory: DepsFactory = default_planning_deps,
    clock: Clock = park_clock,
) -> ParkMindState | Command[Any]:
    """Pause for a human decision, then resolve the proposal accordingly.

    Nothing here before interrupt() may have a side effect: LangGraph
    re-executes this node from the top on resume. Activation only happens
    from this resumed, human-driven path -- never from an agent/LLM node
    (invariant: the LLM never activates a plan; see
    services/ports/plan_repository.py).

    Expects the resume value to be a mapping with a ``"decision"`` key set to
    ``APPROVED``, ``REJECTED``, or ``EDITED``.
    - REJECTED requires an optional ``"rejection_reason"``.
    - EDITED requires an ``"edited_plan"``.
    Anything else is answered with the same interrupt carrying an ``"error"``; 
    the decision is only applied once valid.

    A proposal superseded while the guests were deciding (P0-33) is not decided:
    the guests are told and asked to request a new plan, the candidate and the
    proposal are dropped and ``current_plan`` is kept.
    """
    plan = state.get("candidate_plan")
    proposal = state.get("proposal")
    if not plan or not proposal:
        raise ValueError("No candidate plan/proposal to approve")

    payload: dict[str, Any] = {
        "kind": APPROVAL_INTERRUPT_KIND,
        "candidate_plan": plan,
        "proposal": proposal,
    }
    while True:
        parsed = _parse_approval(interrupt(payload))
        if not isinstance(parsed, str):
            break
        payload = {**payload, "error": parsed}
    
    status, given_reason, edited_plan_data = parsed
    approved = status is ApprovalStatus.APPROVED
    rejection_reason = None if approved or status is ApprovalStatus.EDITED else given_reason or RejectionReason.OTHER
    
    constraints = state.get("constraints")
    guest_ids = constraints.guests if constraints is not None else []

    try:
        ResolveProposalUseCase(deps_factory).execute(
            state["thread_id"],
            proposal.proposal_id,
            status,
            at=clock(),
            guest_ids=guest_ids,
            rejection_reason=rejection_reason,
        )
    except ProposalNoLongerPendingError as exc:
        if exc.status is not status:
            # Superseded by a significant event while the guests decided (section 9
            # [C18]): the decision is dropped, nothing is activated.
            logger.info("decision on %s not applied: %s", proposal.proposal_id, exc)
            message = (
                SUPERSEDED_KEEPS_PLAN_MESSAGE
                if state.get("current_plan")
                else SUPERSEDED_MESSAGE
            )
            return Command(
                update={
                    "messages": [AIMessage(content=message)],
                    "candidate_plan": None,
                    "proposal": None,
                    "approval": None,
                },
                goto=END
            )
        # The same decision was already applied (a re-run after it was stored).

    if status is ApprovalStatus.APPROVED:
        return Command(update=approve_plan(state), goto=END)
    
    if status is ApprovalStatus.REJECTED:
        return Command(update=reject_plan(state, rejection_reason), goto=END)

    if status is ApprovalStatus.EDITED:
        if isinstance(edited_plan_data, dict):
            new_plan = Plan(**edited_plan_data)
        else:
            new_plan = edited_plan_data
            
        from uuid import uuid4
        if new_plan.plan_id == plan.plan_id:
            new_plan = new_plan.model_copy(update={"plan_id": f"plan_{uuid4().hex[:8]}"})

        update_dict = {
            "candidate_plan": new_plan,
            "check_result": None,
            "explanation": None,
            "proposal": None,
            "approval": "EDITED"
        }
        return Command(update=update_dict, goto="check_plan")

    return Command(update={}, goto=END)


def build_planning_stage(
    deps_factory: DepsFactory = default_planning_deps, clock: Clock = park_clock
) -> Any:
    """LOAD CONTEXT -> RESOLVE GROUP -> BUILD PLAN -> CHECK -> EXPLAIN -> PROPOSE -> APPROVAL."""
    graph = StateGraph(ParkMindState)

    def propose_plan(state: ParkMindState) -> ParkMindState:
        return _propose_plan(state, deps_factory=deps_factory, clock=clock)

    def interrupt_approval(state: ParkMindState) -> ParkMindState:
        return _interrupt_for_approval(state, deps_factory=deps_factory, clock=clock)

    def guarded_node(name: str, node: StateNode, then: str | None) -> None:
        destinations = (END,) if then is None else (then, END)
        graph.add_node(name, _guarded(node, then=then), destinations=destinations)

    guarded_node("load_context", _make_load_context_node(deps_factory, clock), "resolve_group")
    guarded_node("resolve_group", _make_resolve_group_node(deps_factory), "build_plan")
    guarded_node("build_plan", _make_build_plan_node(deps_factory, clock), "check_plan")
    guarded_node("check_plan", _make_check_plan_node(deps_factory, clock), None)
    guarded_node("explain", _make_explain_node(deps_factory), "propose_plan")
    guarded_node("propose_plan", cast(StateNode, propose_plan), "interrupt_approval")
    graph.add_node("no_valid_plan", _no_valid_plan)
    graph.add_node("interrupt_approval", interrupt_approval)

    graph.add_edge(START, "load_context")
    graph.add_conditional_edges("check_plan", _route_after_check, ["explain", "no_valid_plan"])
    graph.add_edge("no_valid_plan", END)
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
