"""
Replanning graph (P0-34).

Workflow (architecture v2.2 sections 9 and 36)::

    LOAD PLAN -> HANDLE EVENTS (dedup, apply execution updates, EventPolicy)
        +-> insignificant events ............ IGNORE (a pending proposal stays open)
        +-> user report to confirm .......... CONFIRM EVENTS (interrupt) -> HANDLE CONFIRMED EVENTS
        +-> significant events
              -> LOAD CONTEXT -> RESOLVE GROUP (refresh) -> REPLAN -> CHECK
                   +-> DIFF -> EXPLAIN -> PROPOSE -> APPROVAL (interrupt)   (check passed)
                   +-> NO VALID REPLAN -> END                               (check failed)

Run it on the thread of the initial planning graph with the new events as input
(``{"thread_id": ..., "events": [Event, ...]}``): constraints, guest profiles,
``accessibility_ref`` and the group objective come from that thread's checkpoint.

Guarantees, the same ones as the initial plan's:

* The active plan is read from the repository (``ActivePlanUseCase``), mirrored into
  ``current_plan`` only for the explanation messages. It is replaced only by
  ``_interrupt_for_approval`` -- the human-driven, resumed path -- so a replan is
  always a *proposal*; the LLM never activates anything.
* A replan is a new plan id, proposed only after the same ``ConstraintChecker`` gate as
  an initial plan (``check_replan``: the whole day, counting what was already walked).
  ``propose_plan`` is reachable only from a valid check and re-asserts it.
* A significant event supersedes the proposal that was waiting (inside
  ``HandleEventsUseCase``, in the same transaction that records the event); a run in
  progress is never queued behind it. Rejecting the new proposal leaves the active plan
  exactly as it was.
* Privacy [C19]: state holds only ``accessibility_ref``; every use case reads the
  requirements from the SessionStore.

Interrupts (nothing before an ``interrupt()`` has a side effect: the node re-runs from
the top on resume):

* ``event_confirmation`` -- payload ``{"kind", "events"}``; resume ``{"confirmed": bool}``
  (or a bare bool). A user report that is low-confidence or touches a hard constraint
  is recorded and acted on only once the guests confirm it (section 36); dismissing it
  drops it without a trace.
* ``plan_approval`` -- as in the initial planning graph.

Known limits: REJECT's "update preferences" step is not implemented (the reason is
stored on the proposal); EDIT is P0-32. After a replan finds no valid plan the events
stay recorded, so a duplicate report does not retry it; a new event does.
"""

import logging
from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from parkmind.core.contracts import Event
from parkmind.core.language import Language, text
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.elicitation_graph import StateNode
from parkmind.graph.initial_planning_graph import (
    Clock,
    _guarded,
    _interrupt_for_approval,
    _make_load_context_node,
    _make_resolve_group_node,
    _require_valid_candidate,
    park_clock,
)
from parkmind.graph.state import ParkMindState
from parkmind.graph.state_helpers import (
    active_plan_update,
    discarded_proposal_update,
    language_of,
    propose_plan_change,
)
from parkmind.services.use_cases.active_plan import ActivePlanUseCase
from parkmind.services.use_cases.check_plan import CheckPlanUseCase
from parkmind.services.use_cases.diff_plan import DiffPlanUseCase
from parkmind.services.use_cases.explain_plan import ExplainPlanUseCase
from parkmind.services.use_cases.handle_events import HandledEvents, HandleEventsUseCase
from parkmind.services.use_cases.pending_proposal import PendingProposalUseCase
from parkmind.services.use_cases.planning_deps import DepsFactory, default_planning_deps
from parkmind.services.use_cases.propose_plan import ProposePlanUseCase
from parkmind.services.use_cases.replan import ReplanRequest, ReplanUseCase
from parkmind.services.use_cases.replan_reason import ReplanReasonUseCase

logger = logging.getLogger(__name__)

EVENT_CONFIRMATION_INTERRUPT_KIND = "event_confirmation"
NOTHING_TO_REPLAN_MESSAGE = (
    "There is no confirmed plan for this party yet, so there is nothing to adjust. "
    "Ask me for a plan first."
)


def _ready_to_replan(state: ParkMindState) -> bool:
    return (
        state.get("constraints") is not None
        and bool(state.get("constraints_valid"))
        and not state.get("pending_hard_constraint_confirmation")
        and state.get("group_objective") is not None
        and bool(state.get("thread_id"))
    )


def _significant(events: list[Event]) -> bool:
    return any(event.requires_replan for event in events)


def _after_events(events: list[Event]) -> str:
    return "load_context" if _significant(events) else "ignore_events"


def _events_update(handled: HandledEvents, *, previous: list[Event]) -> dict[str, Any]:
    update: dict[str, Any] = {
        "events": [*previous, *handled.events],
        "held_events": handled.awaiting_confirmation,
    }
    if handled.execution_state is not None:
        update["execution_state"] = handled.execution_state
    if handled.superseded:
        # The proposal the guests were deciding on is dead; do not keep it open in state.
        update.update(discarded_proposal_update())
    return update


def _make_load_plan_node(deps_factory: DepsFactory) -> StateNode:
    use_case = ActivePlanUseCase(deps_factory)

    def load_plan(state: ParkMindState) -> Command[Any]:
        plan = use_case.execute(state["thread_id"]) if state.get("thread_id") else None
        if plan is None or not _ready_to_replan(state):
            return Command(
                update={"messages": [AIMessage(content=text("nothing_to_replan", language_of(state)))]},
                goto=END,
            )
        return Command(update=active_plan_update(plan), goto="handle_events")

    return cast(StateNode, load_plan)


def _make_handle_events_node(deps_factory: DepsFactory, clock: Clock) -> StateNode:
    use_case = HandleEventsUseCase(deps_factory)

    def handle_events(state: ParkMindState) -> Command[Any]:
        plan = state.get("current_plan")
        constraints = state.get("constraints")
        objective = state.get("group_objective")
        if plan is None or constraints is None or objective is None:
            raise ValueError("the active plan, constraints and objective are required")
        handled = use_case.execute(
            state["thread_id"],
            list(state.get("events") or []),
            plan=plan,
            constraints=constraints,
            objective=objective,
            accessibility_ref=state.get("accessibility_ref") or [],
            confirmed=False,
            now=clock(),
        )
        update = _events_update(handled, previous=[])
        update["rest_first"] = handled.rest_first
        update["event_confirmation"] = (
            "PENDING" if handled.awaiting_confirmation else None
        )
        if handled.awaiting_confirmation:
            return Command(update=update, goto="confirm_events")
        return Command(update=update, goto=_after_events(handled.events))

    return cast(StateNode, handle_events)


def _parse_confirmation(value: Any) -> bool | str:
    """The guests' answer, or why the resume value is not valid."""
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("confirmed"), bool):
        return cast(bool, value["confirmed"])
    return "the answer must be a boolean or a mapping with a boolean 'confirmed' key"


def _confirm_events(state: ParkMindState) -> Command[Any]:
    """Ask the guests to confirm the reports that were set aside.

    The resume value is validated before anything happens: an invalid one is answered
    with the same interrupt carrying an ``"error"``.
    """
    payload: dict[str, Any] = {
        "kind": EVENT_CONFIRMATION_INTERRUPT_KIND,
        "events": list(state.get("held_events") or []),
    }
    while True:
        parsed = _parse_confirmation(interrupt(payload))
        if isinstance(parsed, bool):
            break
        payload = {**payload, "error": parsed}
    if parsed:
        return Command(
            update={"event_confirmation": "CONFIRMED"}, goto="handle_confirmed_events"
        )
    return Command(
        update={"event_confirmation": "DISMISSED", "held_events": []},
        goto=_after_events(list(state.get("events") or [])),
    )


def _make_handle_confirmed_events_node(
    deps_factory: DepsFactory, clock: Clock
) -> StateNode:
    use_case = HandleEventsUseCase(deps_factory)

    def handle_confirmed_events(state: ParkMindState) -> Command[Any]:
        plan = state.get("current_plan")
        constraints = state.get("constraints")
        objective = state.get("group_objective")
        if plan is None or constraints is None or objective is None:
            raise ValueError("the active plan, constraints and objective are required")
        previous = list(state.get("events") or [])
        handled = use_case.execute(
            state["thread_id"],
            list(state.get("held_events") or []),
            plan=plan,
            constraints=constraints,
            objective=objective,
            accessibility_ref=state.get("accessibility_ref") or [],
            confirmed=True,
            now=clock(),
        )
        update = _events_update(handled, previous=previous)
        update["rest_first"] = bool(state.get("rest_first")) or handled.rest_first
        return Command(update=update, goto=_after_events(update["events"]))

    return cast(StateNode, handle_confirmed_events)


def _make_ignore_events_node(deps_factory: DepsFactory) -> StateNode:
    use_case = PendingProposalUseCase(deps_factory)

    def ignore_events(state: ParkMindState) -> Command[Any]:
        """Nothing significant happened: the plan and any open proposal are left alone.

        A run on a thread does not carry the interrupt of the run before it, so a proposal
        still waiting for the guests (read from the repository, the source of truth) is
        put back in front of them instead of being left without a way to be decided.
        """
        pending = use_case.execute(state["thread_id"])
        if pending is None:
            return Command(goto=END)
        reopened = propose_plan_change(
            cast(ParkMindState, dict(state)), pending.proposal
        )
        reopened["candidate_plan"] = pending.candidate_plan
        return Command(update=dict(reopened), goto="interrupt_approval")

    return cast(StateNode, ignore_events)


def _not_proposed(lang: Language, *lines: str) -> str:
    return "\n".join(
        [text("replan_not_proposed", lang), *lines, text("keeps_plan", lang)]
    )


def _make_replan_node(deps_factory: DepsFactory, clock: Clock) -> StateNode:
    use_case = ReplanUseCase(deps_factory)

    def replan(state: ParkMindState) -> Command[Any]:
        lang = language_of(state)
        plan = state.get("current_plan")
        constraints = state.get("constraints")
        objective = state.get("group_objective")
        live_context = state.get("live_context")
        if (
            plan is None
            or constraints is None
            or objective is None
            or live_context is None
        ):
            raise ValueError(
                "the active plan, constraints, objective and live context are required"
            )
        outcome = use_case.execute(
            ReplanRequest(
                thread_id=state["thread_id"],
                plan=plan,
                constraints=constraints,
                profiles=state.get("guest_profiles") or [],
                accessibility_ref=state.get("accessibility_ref") or [],
                live_context=live_context,
                objective=objective,
                now=clock(),
                rest_first=bool(state.get("rest_first")),
            )
        )
        result = outcome.result
        update: dict[str, Any] = {
            "live_context": outcome.live_context,
            "execution_state": outcome.execution_state,
            "check_result": None,
            "explanation": None,
            "diff": None,
            **discarded_proposal_update(),
        }
        if not result.valid or result.plan is None:
            lines = [f"- {result.fatal_error}"] if result.fatal_error else []
            if result.unmet_must_do:
                names = ", ".join(result.unmet_must_do)
                lines.append("- " + text("unmet_must_do", lang, names=names))
            update["messages"] = [AIMessage(content=_not_proposed(lang, *lines))]
            return Command(update=update, goto=END)
        update["candidate_plan"] = result.plan
        return Command(update=update, goto="check_plan")

    return cast(StateNode, replan)


def _make_check_replan_node(deps_factory: DepsFactory) -> StateNode:
    use_case = CheckPlanUseCase(deps_factory)

    def check_plan(state: ParkMindState) -> Command[Any]:
        plan = state.get("candidate_plan")
        constraints = state.get("constraints")
        live_context = state.get("live_context")
        execution_state = state.get("execution_state")
        if (
            plan is None
            or constraints is None
            or live_context is None
            or execution_state is None
        ):
            raise ValueError(
                "a candidate plan, constraints, live context and progress are required"
            )
        result = use_case.execute_replan(
            thread_id=state["thread_id"],
            plan=plan,
            constraints=constraints,
            accessibility_ref=state.get("accessibility_ref") or [],
            live_context=live_context,
            execution_state=execution_state,
        )
        if result.valid:
            return Command(update={"check_result": result}, goto="diff")
        reasons = [f"- {v.message}" for v in result.violations]
        return Command(
            update={
                "check_result": result,
                "candidate_plan": None,
                "messages": [AIMessage(content=_not_proposed(language_of(state), *reasons))],
            },
            goto=END,
        )

    return cast(StateNode, check_plan)


def _make_diff_node() -> StateNode:
    use_case = DiffPlanUseCase()

    def diff(state: ParkMindState) -> dict[str, Any]:
        current = state.get("current_plan")
        candidate = state.get("candidate_plan")
        if current is None or candidate is None:
            raise ValueError("the active plan and a candidate plan are required")
        _require_valid_candidate(state)
        return {
            "diff": use_case.execute(current_plan=current, candidate_plan=candidate)
        }

    return diff


def _make_explain_node(deps_factory: DepsFactory) -> StateNode:
    explainer = ExplainPlanUseCase(deps_factory)
    reasons = ReplanReasonUseCase(deps_factory)

    def explain(state: ParkMindState) -> dict[str, Any]:
        plan = state.get("candidate_plan")
        check = state.get("check_result")
        diff = state.get("diff")
        if plan is None or check is None or diff is None:
            raise ValueError("a checked candidate plan and its diff are required")
        lang = language_of(state)
        reason = reasons.execute(state.get("events") or [], lang)
        constraints = state.get("constraints")
        guests = constraints.guests if constraints is not None else []
        return {
            "explanation": explainer.execute_replan(
                plan, check, reason=reason.text, diff=diff, guests=guests, lang=lang
            )
        }

    return explain


def _propose_replan(
    state: ParkMindState, *, deps_factory: DepsFactory, clock: Clock
) -> ParkMindState:
    """Persist the candidate and open a PENDING proposal against the active plan.

    Only a plan that passed the checker, with its explanation and diff, may be proposed.
    """
    plan = state.get("candidate_plan")
    current = state.get("current_plan")
    diff = state.get("diff")
    if plan is None or current is None or diff is None:
        raise ValueError(
            "a candidate plan, the active plan and their diff are required"
        )
    _require_valid_candidate(state)

    reason = ReplanReasonUseCase(deps_factory).execute(
        state.get("events") or [], language_of(state)
    )
    proposal = ProposePlanUseCase(deps_factory).execute(
        state["thread_id"],
        plan,
        proposal_id=f"prop_{uuid4().hex[:8]}",
        reason=reason.text,
        explanation=state.get("explanation") or "",
        at=clock(),
        base_plan_id=current.plan_id,
        diff=diff,
        triggering_event_id=reason.triggering_event_id,
    )
    return propose_plan_change(state, proposal)


def build_replanning_graph(
    checkpointer: Any = None,
    *,
    deps_factory: DepsFactory = default_planning_deps,
    clock: Clock = park_clock,
) -> Any:
    """Compile the replanning graph. Nothing is opened here: ports are created per node."""
    graph = StateGraph(ParkMindState)

    def propose_plan(state: ParkMindState) -> ParkMindState:
        return _propose_replan(state, deps_factory=deps_factory, clock=clock)

    def interrupt_approval(state: ParkMindState) -> ParkMindState:
        return _interrupt_for_approval(state, deps_factory=deps_factory, clock=clock)

    def routed(name: str, node: StateNode, destinations: tuple[str, ...]) -> None:
        graph.add_node(
            name, _guarded(node, then=None), destinations=(*destinations, END)
        )

    routed("load_plan", _make_load_plan_node(deps_factory), ("handle_events",))
    routed(
        "handle_events",
        _make_handle_events_node(deps_factory, clock),
        ("confirm_events", "load_context", "ignore_events"),
    )
    graph.add_node(
        "confirm_events",
        cast(StateNode, _confirm_events),
        destinations=("handle_confirmed_events", "load_context", "ignore_events"),
    )
    routed(
        "handle_confirmed_events",
        _make_handle_confirmed_events_node(deps_factory, clock),
        ("load_context", "ignore_events"),
    )
    graph.add_node(
        "ignore_events",
        _guarded(_make_ignore_events_node(deps_factory), then=None),
        destinations=("interrupt_approval", END),
    )
    graph.add_node(
        "load_context",
        _guarded(_make_load_context_node(deps_factory, clock), then="resolve_group"),
        destinations=("resolve_group", END),
    )
    graph.add_node(
        "resolve_group",
        _guarded(_make_resolve_group_node(deps_factory), then="replan"),
        destinations=("replan", END),
    )
    routed("replan", _make_replan_node(deps_factory, clock), ("check_plan",))
    routed("check_plan", _make_check_replan_node(deps_factory), ("diff",))
    graph.add_node(
        "diff",
        _guarded(_make_diff_node(), then="explain"),
        destinations=("explain", END),
    )
    graph.add_node(
        "explain",
        _guarded(_make_explain_node(deps_factory), then="propose_plan"),
        destinations=("propose_plan", END),
    )
    graph.add_node(
        "propose_plan",
        _guarded(cast(StateNode, propose_plan), then="interrupt_approval"),
        destinations=("interrupt_approval", END),
    )
    graph.add_node("interrupt_approval", interrupt_approval)

    graph.add_edge(START, "load_plan")
    graph.add_edge("interrupt_approval", END)
    return graph.compile(checkpointer=checkpointer or default_checkpointer())
