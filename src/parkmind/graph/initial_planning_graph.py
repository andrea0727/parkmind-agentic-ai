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
``elicitation_graph``; ``approval``: ``{"decision": "APPROVED" | "REJECTED" |
"EDITED", "rejection_reason": <RejectionReason value>, "edit": {"remove": [stop
node ids], "add": [attraction node ids]}}`` -- the reason only for a rejection, the
edit only (and always) for ``EDITED``. The approval interrupt payload carries
``"kind": "plan_approval"``; a resume value that does not fit, or an edit that does
not fit the plan, is answered with the same interrupt plus an ``"error"``, before
any side effect.

An edit (section 23) is never a plan: ``ApplyEditUseCase`` turns it into the party's
constraints (dropped stops avoided, added attractions must-dos), the edited proposal
is resolved as ``EDITED`` -- it never activates anything -- and planning starts again
at ``load_context``, so the new candidate is built, checked, explained and proposed
as a new PENDING proposal like any other. If no valid plan fits the edit, the guests
are told why and get back what they had: the plan before the edit, with the
constraints before it, goes through CHECK again (on the fresh data) and is proposed
anew, under a new plan id. If even that no longer passes, nothing is proposed, as after any failed check.

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
from pydantic import ValidationError

from parkmind.agents.elicit_agent import ELICIT_PROMPT_VERSION, GuestInfoExtractor
from parkmind.agents.explain_agent import (
    AnthropicNarrator,
    PlanNarrator,
    explain_grounded,
)
from parkmind.config.tracing import tracing_guard
from parkmind.core.contracts import (
    PARK_TZ,
    ApprovalStatus,
    PartyConstraints,
    Plan,
    RejectionReason,
    StopKind,
)
from parkmind.core.language import DEFAULT_LANGUAGE, text
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.elicitation_graph import (
    StateNode,
    build_elicitation_graph,
    ensure_confirmed,
)
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import (
    approve_plan,
    explained,
    language_of,
    propose_plan_change,
    reject_plan,
    solo_of,
)
from parkmind.services.use_cases.accessibility_intake import AccessibilityIntakeUseCase
from parkmind.services.use_cases.apply_edit import (
    AppliedEdit,
    ApplyEditUseCase,
    InvalidEditError,
    PlanEdit,
)
from parkmind.services.use_cases.build_plan import BuildPlanUseCase
from parkmind.services.use_cases.check_plan import CheckPlanUseCase
from parkmind.services.use_cases.explain_plan import ExplainPlanUseCase
from parkmind.services.use_cases.load_context import LoadContextUseCase
from parkmind.services.use_cases.park_status import ParkClosedError, ParkStatusUseCase
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
EDIT_REENTRY = "load_context"
"""Where both planning graphs start again after an edit: fresh data, then the same path."""
_SUPERSEDED = (
    "Something in the park changed while you were deciding, so this proposal was "
    "set aside and nothing was activated."
)
_ASK_AGAIN = "Ask me for a new plan and I'll build it from the latest park information."
SUPERSEDED_MESSAGE = f"{_SUPERSEDED} {_ASK_AGAIN}"
SUPERSEDED_KEEPS_PLAN_MESSAGE = (
    f"{_SUPERSEDED} Your current plan stays as it was. {_ASK_AGAIN}"
)
EDITING_MESSAGE = "Updating the plan with your changes."
EDIT_DID_NOT_FIT_MESSAGE = (
    "I couldn't fit your changes into a plan that meets every requirement:"
)
EDIT_REOFFER_MESSAGE = "Here is the plan you had before, checked again. You can approve, edit or reject it."
EDIT_CLEARED: ParkMindState = {
    "constraints_before_edit": None,
    "plan_before_edit": None,
}
"""Drops what an edit kept to fall back on: set on every path that ends the edit."""
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
        raise CheckNotPassedError(
            "the candidate plan has not passed the constraint checker"
        )


def _guarded(node: StateNode, *, then: str | None) -> StateNode:
    """Turn a ``PlanningUnavailableError`` into a message and the end of the stage.

    A static edge would still fire after a ``Command(goto=END)``, so the node moves
    on to ``then`` itself (``None``: a conditional edge decides).
    """

    def guarded(state: ParkMindState) -> dict[str, Any] | Command[Any]:
        try:
            update = node(state)
        except ParkClosedError as closed:
            message = text(
                "park_closed",
                language_of(state),
                solo=solo_of(state),
                closing=closed.status.closing_label(),
            )
            return Command(
                update={
                    "messages": [AIMessage(content=message)],
                    "candidate_plan": None,
                    "check_result": None,
                    "explanation": None,
                    "provisional_offer": True,
                },
                goto=END,
            )
        except PlanningUnavailableError:
            logger.exception("planning is unavailable")
            return Command(
                update={
                    "messages": [
                        AIMessage(
                            content=text(
                                "unavailable", language_of(state), solo=solo_of(state)
                            )
                        )
                    ],
                    "candidate_plan": None,
                    "check_result": None,
                    "explanation": None,
                    **EDIT_CLEARED,
                },
                goto=END,
            )
        return update if then is None else Command(update=update, goto=then)

    return cast(StateNode, guarded)


def _make_load_context_node(deps_factory: DepsFactory, clock: Clock) -> StateNode:
    use_case = LoadContextUseCase(deps_factory)
    park_status = ParkStatusUseCase(deps_factory)

    def load_context(state: ParkMindState) -> dict[str, Any]:
        ensure_confirmed(state)
        park_status.ensure_open(clock())
        loaded = use_case.execute(
            state["thread_id"], state.get("accessibility_ref") or [], clock()
        )
        return {"live_context": loaded.for_state(), "provisional_offer": False}

    return load_context


def _make_resolve_group_node(deps_factory: DepsFactory) -> StateNode:
    use_case = BuildPlanUseCase(deps_factory)

    def resolve_group(state: ParkMindState) -> dict[str, Any]:
        ensure_confirmed(state)
        constraints = state["constraints"]
        live_context = state.get("live_context")
        if constraints is None or live_context is None:
            raise ValueError(
                "constraints and live context must be loaded before resolving"
            )
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
            raise ValueError(
                "constraints, live context and group objective are required"
            )
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
            "candidate_plan": _with_elicit_version(built.plan),
            "live_context": built.live_context,
            "check_result": None,
            "explanation": None,
        }

    return build_plan


def _with_elicit_version(plan: Plan) -> Plan:
    """Record which elicit prompt read the constraints this plan was built from.

    A replan copies the plan's provenance, so the version travels with it.
    """
    provenance = plan.provenance
    versions = {**provenance.prompt_versions, "elicit": ELICIT_PROMPT_VERSION}
    return plan.model_copy(
        update={"provenance": provenance.model_copy(update={"prompt_versions": versions})}
    )


def _make_check_plan_node(deps_factory: DepsFactory, clock: Clock) -> StateNode:
    use_case = CheckPlanUseCase(deps_factory)

    def check_plan(state: ParkMindState) -> dict[str, Any]:
        ensure_confirmed(state)
        plan = state.get("candidate_plan")
        constraints = state["constraints"]
        live_context = state.get("live_context")
        if plan is None or constraints is None or live_context is None:
            raise ValueError(
                "a candidate plan, constraints and live context are required"
            )
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


def before_edit_update(
    state: ParkMindState, reasons: list[str]
) -> dict[str, Any] | None:
    """After an edit no valid plan fits: put back what the guests had, to be checked again.

    ``None`` when the failed plan did not come from an edit. The caller routes to its
    CHECK node, so the plan is proposed again only if it still passes on fresh data.

    The plan comes back under a new id: EXPLAIN writes it again and records which
    prompt did (``explained``), and a stored plan body never changes, so the plan
    first proposed cannot be saved again with another explanation's version.
    """
    plan = state.get("plan_before_edit")
    constraints = state.get("constraints_before_edit")
    if plan is None or constraints is None:
        return None
    message = "\n".join([EDIT_DID_NOT_FIT_MESSAGE, *reasons, EDIT_REOFFER_MESSAGE])
    return {
        "messages": [AIMessage(content=message)],
        "candidate_plan": plan.model_copy(
            update={"plan_id": f"plan_{uuid4().hex[:12]}"}
        ),
        "constraints": constraints,
        "check_result": None,
        "explanation": None,
        "diff": None,
        **EDIT_CLEARED,
    }


def _no_valid_plan(state: ParkMindState) -> Command[Any]:
    """Tell the user why nothing was proposed; ``check_result`` stays in state.

    A plan that failed after an edit gives way to the plan before the edit instead.
    """
    check = state.get("check_result")
    reasons = [f"- {v.message}" for v in check.violations] if check is not None else []
    restored = before_edit_update(state, reasons)
    if restored is not None:
        return Command(update=restored, goto="check_plan")
    lang = language_of(state)
    lines = [text("no_valid_plan", lang)]
    lines += reasons
    lines.append(text("relax_hint", lang, solo=solo_of(state)))
    return Command(
        update={
            "messages": [AIMessage(content="\n".join(lines))],
            "candidate_plan": None,
        },
        goto=END,
    )


def _make_explain_node(
    deps_factory: DepsFactory, narrator: PlanNarrator | None
) -> StateNode:
    """EXPLAIN (section 40, P0-31): the narrator's grounded text, else the template."""
    use_case = ExplainPlanUseCase(deps_factory)

    def explain(state: ParkMindState) -> dict[str, Any]:
        plan = state.get("candidate_plan")
        check = state.get("check_result")
        if plan is None or check is None:
            raise ValueError("a checked candidate plan is required")
        constraints = state.get("constraints")
        lang = language_of(state)
        prepared = use_case.prepare(
            plan,
            check,
            guests=constraints.guests if constraints is not None else [],
            must_do=constraints.must_do if constraints is not None else [],
            objective=state.get("group_objective"),
            lang=lang,
        )
        # The narrator writes English only; other languages get the localized template.
        active = narrator if lang == DEFAULT_LANGUAGE else None
        return explained(
            plan,
            explain_grounded(active, prepared, thread_id=state["thread_id"]),
            {"elicit": ELICIT_PROMPT_VERSION},
        )

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
    explanation, may be proposed, and only for a party with guests (``require_party``,
    checked before anything is saved).
    """
    plan = state.get("candidate_plan")
    if not plan:
        raise ValueError("No candidate plan to propose")
    _require_valid_candidate(state)
    require_party(state)

    proposal = ProposePlanUseCase(deps_factory).execute(
        state["thread_id"],
        plan,
        proposal_id=f"prop_{uuid4().hex[:8]}",
        reason=text("initial_plan_reason", language_of(state)),
        explanation=state.get("explanation") or "",
        at=clock(),
    )
    return {**propose_plan_change(state, proposal), **EDIT_CLEARED}


_NOT_DECIDABLE = frozenset({ApprovalStatus.PENDING, ApprovalStatus.SUPERSEDED})


def _parse_approval(
    value: Any,
) -> tuple[ApprovalStatus, RejectionReason | None, PlanEdit | None] | str:
    """The decision, the rejection reason and the edit given, or why the value is not valid."""
    if not isinstance(value, Mapping):
        return "the decision must be a mapping with a 'decision' key"
    try:
        status = ApprovalStatus(value.get("decision"))
    except ValueError:
        return "'decision' must be APPROVED, REJECTED or EDITED"
    if status in _NOT_DECIDABLE:
        return "'decision' must be APPROVED, REJECTED or EDITED"
    if status is not ApprovalStatus.EDITED and value.get("edit") is not None:
        return "an 'edit' is only taken with an EDITED decision"
    edit: PlanEdit | None = None
    if status is ApprovalStatus.EDITED:
        raw_edit = value.get("edit")
        if not isinstance(raw_edit, Mapping):
            return "an EDITED decision needs an 'edit' with 'remove' and/or 'add'"
        try:
            edit = PlanEdit.model_validate(raw_edit)
        except ValidationError:
            return "'edit' takes only 'remove' and 'add', each a list of node ids"
    raw_reason = value.get("rejection_reason")
    if status is not ApprovalStatus.REJECTED or raw_reason in (None, ""):
        return status, None, edit
    try:
        return status, RejectionReason(raw_reason), edit
    except ValueError:
        return "'rejection_reason' is not a known reason"


def _apply_edit(
    state: ParkMindState, plan: Plan, edit: PlanEdit, deps_factory: DepsFactory
) -> AppliedEdit | str:
    """The edited constraints, or why the edit cannot be applied (nothing changed).

    Missing park data raises instead of asking again: on resume LangGraph replays the
    answers in order, so whether an answer was refused must not depend on the network
    (a refusal that a replay did not repeat would shift every later answer by one).
    The run fails and LangGraph keeps the answer: retrying the run with no new input
    (``invoke(None, config)``) applies it once the data is back. Resume by interrupt
    id (``Command(resume={interrupt.id: value})``): inside this subgraph, a plain
    resume value kept from a failed run would also answer the next interrupt.
    """
    constraints = state.get("constraints")
    if constraints is None:
        raise ValueError("an edit needs the party's constraints")
    try:
        return ApplyEditUseCase(deps_factory).execute(
            constraints, plan, edit, execution_state=state.get("execution_state")
        )
    except InvalidEditError as exc:
        return str(exc)


def require_party(state: ParkMindState) -> PartyConstraints:
    """The party's constraints, with at least one guest: every decision on a proposal
    is logged per guest (section 23), so nothing is proposed or decided for nobody."""
    constraints = state.get("constraints")
    if constraints is None or not constraints.guests:
        raise ValueError("a proposal needs the party's guests")
    return constraints


def _attraction_ids(plan: Plan) -> list[str]:
    return [
        stop.node_id
        for stop in plan.stops
        if stop.kind in (StopKind.ATTRACTION, StopKind.SHOW)
    ]


def _interrupt_for_approval(
    state: ParkMindState,
    *,
    deps_factory: DepsFactory = default_planning_deps,
    clock: Clock = park_clock,
) -> Command[Any]:
    """Pause for a human decision, then resolve the proposal accordingly.

    Nothing here before interrupt() may have a side effect: LangGraph
    re-executes this node from the top on resume. Activation only happens
    from this resumed, human-driven path -- never from an agent/LLM node
    (invariant: the LLM never activates a plan; see
    services/ports/plan_repository.py).

    Expects the resume value described in the module docstring. Anything else --
    including an edit that does not fit the plan -- is answered with the same
    interrupt carrying an ``"error"``; the decision is only applied once valid.

    APPROVED and REJECTED end the stage. EDITED (section 35: "EDIT -> LOG BEHAVIOR
    -> APPLY EDIT -> CHECK -> PROPOSE") is resolved and logged first, then the
    edited constraints go to ``load_context`` and the new candidate goes through
    the checker like any plan. If no valid plan fits the edit, the plan before it is
    checked again and re-offered (``before_edit_update``).

    A proposal superseded while the guests were deciding (P0-33) is not decided:
    the guests are told and asked to request a new plan, the candidate and the
    proposal are dropped and ``current_plan`` is kept.
    """
    plan = state.get("candidate_plan")
    proposal = state.get("proposal")
    if not plan or not proposal:
        raise ValueError("No candidate plan/proposal to approve")
    constraints = require_party(state)

    payload: dict[str, Any] = {
        "kind": APPROVAL_INTERRUPT_KIND,
        "candidate_plan": plan,
        "proposal": proposal,
    }
    applied: AppliedEdit | None = None
    while True:
        parsed = _parse_approval(interrupt(payload))
        if not isinstance(parsed, str):
            status, given_reason, edit = parsed
            if edit is None:
                break
            edited = _apply_edit(state, plan, edit, deps_factory)
            if not isinstance(edited, str):
                applied = edited
                break
            parsed = edited
        payload = {**payload, "error": parsed}
    rejection_reason: RejectionReason | None = None
    if status is ApprovalStatus.REJECTED:
        rejection_reason = given_reason or RejectionReason.OTHER

    try:
        ResolveProposalUseCase(deps_factory).execute(
            state["thread_id"],
            proposal.proposal_id,
            status,
            at=clock(),
            rejection_reason=rejection_reason,
            guests=constraints.guests,
            attraction_ids=(
                applied.changed_ids if applied is not None else _attraction_ids(plan)
            ),
        )
    except ProposalNoLongerPendingError as exc:
        if exc.status is not status:
            # Superseded by a significant event while the guests decided (section 9
            # [C18]): the decision is dropped, nothing is activated.
            logger.info("decision on %s not applied: %s", proposal.proposal_id, exc)
            lang = language_of(state)
            parts = [text("superseded", lang, solo=solo_of(state))]
            if state.get("current_plan"):
                parts.append(text("keeps_plan", lang, solo=solo_of(state)))
            parts.append(text("ask_again", lang, solo=solo_of(state)))
            message = " ".join(parts)
            return Command(
                update={
                    "messages": [AIMessage(content=message)],
                    "candidate_plan": None,
                    "proposal": None,
                    "approval": None,
                },
                goto=END,
            )
        # The same decision was already applied (a re-run after it was stored).

    if applied is not None:
        return Command(
            update={
                "messages": [AIMessage(content=EDITING_MESSAGE)],
                "constraints": applied.constraints,
                "constraints_before_edit": constraints,
                "plan_before_edit": plan,
                "candidate_plan": None,
                "check_result": None,
                "explanation": None,
                "diff": None,
                "proposal": None,
                "approval": ApprovalStatus.EDITED.value,
            },
            goto=EDIT_REENTRY,
        )
    if rejection_reason is None:
        return Command(update=approve_plan(state), goto=END)
    return Command(update=reject_plan(state, rejection_reason), goto=END)


def build_planning_stage(
    deps_factory: DepsFactory = default_planning_deps,
    clock: Clock = park_clock,
    narrator: PlanNarrator | None = None,
) -> Any:
    """LOAD CONTEXT -> RESOLVE GROUP -> BUILD PLAN -> CHECK -> EXPLAIN -> PROPOSE -> APPROVAL.

    Built on its own, the stage's EXPLAIN uses the template unless a ``narrator`` is
    given; ``build_initial_planning_graph``, the graph the app runs, passes Claude by
    default. Refuses to build with tracing switched on (``tracing_guard``), like
    every graph builder.
    """
    tracing_guard()
    graph = StateGraph(ParkMindState)

    def propose_plan(state: ParkMindState) -> ParkMindState:
        return _propose_plan(state, deps_factory=deps_factory, clock=clock)

    def interrupt_approval(state: ParkMindState) -> Command[Any]:
        return _interrupt_for_approval(state, deps_factory=deps_factory, clock=clock)

    def guarded_node(name: str, node: StateNode, then: str | None) -> None:
        destinations = (END,) if then is None else (then, END)
        graph.add_node(name, _guarded(node, then=then), destinations=destinations)

    guarded_node(
        "load_context", _make_load_context_node(deps_factory, clock), "resolve_group"
    )
    guarded_node("resolve_group", _make_resolve_group_node(deps_factory), "build_plan")
    guarded_node("build_plan", _make_build_plan_node(deps_factory, clock), "check_plan")
    guarded_node("check_plan", _make_check_plan_node(deps_factory, clock), None)
    guarded_node("explain", _make_explain_node(deps_factory, narrator), "propose_plan")
    guarded_node("propose_plan", cast(StateNode, propose_plan), "interrupt_approval")
    graph.add_node("no_valid_plan", _no_valid_plan, destinations=("check_plan", END))
    graph.add_node(
        "interrupt_approval", interrupt_approval, destinations=(EDIT_REENTRY, END)
    )

    graph.add_edge(START, "load_context")
    graph.add_conditional_edges(
        "check_plan", _route_after_check, ["explain", "no_valid_plan"]
    )
    return graph.compile()


def build_initial_planning_graph(
    extractor: GuestInfoExtractor | None = None,
    intake: AccessibilityIntakeUseCase | None = None,
    checkpointer: Any = None,
    *,
    deps_factory: DepsFactory = default_planning_deps,
    clock: Clock = park_clock,
    names: AttractionNameResolver | None = None,
    narrator: PlanNarrator | None = None,
) -> Any:
    """Compile the end-to-end initial planning graph.

    Nothing is opened here: connections and provider clients are created per node
    by ``deps_factory``, so building the graph needs no database or network.
    EXPLAIN uses ``narrator``, Claude by default (like elicit's extractor); without a
    key, or whenever its text is not grounded, the template stands in.
    """
    resolver = names or AttractionNameResolver(
        deferred_attraction_repository(deps_factory), MAGIC_KINGDOM_PARK_ID
    )
    return build_elicitation_graph(
        extractor,
        intake,
        checkpointer or default_checkpointer(),
        cast(
            StateNode,
            build_planning_stage(deps_factory, clock, narrator or AnthropicNarrator()),
        ),
        names=resolver,
    )
