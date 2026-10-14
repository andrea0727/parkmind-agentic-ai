"""ConversationService: the one place that knows which graph owns a thread (P0-35).

A thread starts in the initial planning graph; once a plan exists, events run the
replanning graph on the same thread, and whichever graph last stopped for a human owns the
interrupt the next answer must resume. LangGraph does not carry an interrupt from one
graph to another, so the service remembers, per thread, who is waiting and for what, and
refuses an answer that does not fit (``NotExpectedError``) instead of resuming blindly.

Calls on one thread are serialized; different threads run in parallel. State lives in this
process (``default_checkpointer`` is in-memory), which is why the API runs one worker.

Tracing (ADR 0003): the ``callbacks`` given at construction -- the masked Langfuse handler,
when ``server.py`` switches it on -- go into every graph run's config, with the thread as
the Langfuse session and the graph as a tag. Nodes and LLM calls inherit them.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from parkmind.core.contracts import (
    PARK_TZ,
    ApprovalStatus,
    Event,
    Plan,
    Proposal,
    RejectionReason,
)
from parkmind.core.conversation import (
    ConfirmationRequest,
    HardConstraintAnswer,
    NotExpectedError,
    PlanEditRequest,
    ServiceUnavailableError,
    StaleProposalError,
    Turn,
    TurnStatus,
    UnknownAttractionError,
    UnknownThreadError,
)
from parkmind.core.language import DEFAULT_LANGUAGE, Language, as_language, text
from parkmind.graph.initial_planning_graph import APPROVAL_INTERRUPT_KIND, park_clock
from parkmind.graph.replanning_graph import EVENT_CONFIRMATION_INTERRUPT_KIND
from parkmind.graph.state_helpers import solo_of
from parkmind.services.use_cases.active_plan import ActivePlanUseCase
from parkmind.services.use_cases.park_status import ParkStatusUseCase
from parkmind.services.use_cases.pending_proposal import PendingProposalUseCase
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    PlanningUnavailableError,
    default_planning_deps,
    load_catalog,
    open_deps,
)
from parkmind.services.use_cases.provisional_plan import ProvisionalPlanUseCase, retimed
from parkmind.services.use_cases.resolve_attraction_names import suggest_attractions

logger = logging.getLogger(__name__)

MISSING_INFORMATION_KIND = "missing_information"
HARD_CONSTRAINT_KIND = "hard_constraint_confirmation"

Owner = Literal["initial", "replanning"]


@dataclass
class _Thread:
    lock: threading.RLock
    owner: Owner = "initial"
    waiting_for: str | None = None
    proposal_id: str | None = None
    seen_messages: int = 0
    language_hinted: bool = False
    language: Language = DEFAULT_LANGUAGE
    saved_party: str | None = None
    provisional_opens: datetime | None = None
    interrupt_id: str | None = None
    """The interrupt the thread is paused on; an answer resumes it by this id."""
    unapplied: Any = None
    """An answer the graph took but whose run failed: LangGraph keeps it, so the run is
    retried with no input instead of answering again."""
    has_unapplied: bool = False


def _new_thread_id() -> str:
    return f"thr_{uuid4().hex[:12]}"


class ConversationService:
    def __init__(
        self,
        initial_graph: Any,
        replanning_graph: Any,
        *,
        deps_factory: DepsFactory = default_planning_deps,
        new_thread_id: Callable[[], str] = _new_thread_id,
        clock: Callable[[], datetime] = park_clock,
        callbacks: Sequence[Any] = (),
    ) -> None:
        self._graphs: dict[Owner, Any] = {
            "initial": initial_graph,
            "replanning": replanning_graph,
        }
        self._deps_factory = deps_factory
        self._clock = clock
        self._provisional = ProvisionalPlanUseCase(deps_factory)
        self._park_status = ParkStatusUseCase(deps_factory)
        self._pending = PendingProposalUseCase(deps_factory)
        self._active = ActivePlanUseCase(deps_factory)
        self._new_thread_id = new_thread_id
        self._callbacks = list(callbacks)
        self._threads: dict[str, _Thread] = {}
        self._registry_lock = threading.Lock()

    def start(self, saved_party: str | None = None) -> str:
        thread_id = self._new_thread_id()
        with self._registry_lock:
            self._threads[thread_id] = _Thread(
                lock=threading.RLock(), saved_party=saved_party
            )
        return thread_id

    def say(self, thread_id: str, text: str, lang: str | None = None) -> Turn:
        thread = self._thread(thread_id)
        with thread.lock:
            if thread.waiting_for == MISSING_INFORMATION_KIND:
                return self._answer(thread_id, thread, "initial", text)
            self._require_nothing_pending(thread)
            payload: dict[str, Any] = {
                "thread_id": thread_id,
                "messages": [HumanMessage(content=text)],
            }
            if thread.saved_party:
                payload["messages"].insert(0, HumanMessage(content=thread.saved_party))
                thread.saved_party = None
            hinting = not thread.language_hinted
            if hinting:
                thread.language = as_language(lang)
                payload["language"] = thread.language
            turn = self._run(thread_id, thread, "initial", payload)
            if hinting:
                thread.language_hinted = True
                turn = self._with_closed_notice(thread, turn)
            return turn

    def confirm_constraints(self, thread_id: str, answer: HardConstraintAnswer) -> Turn:
        thread = self._thread(thread_id)
        with thread.lock:
            self._require_waiting(thread, HARD_CONSTRAINT_KIND)
            resume = {
                "confirmed": answer.confirmed,
                "consent": answer.consent,
                "retention_policy": answer.retention_policy,
                "correction": answer.correction,
            }
            return self._answer(thread_id, thread, "initial", resume)

    def decide(
        self,
        thread_id: str,
        proposal_id: str,
        decision: ApprovalStatus,
        rejection_reason: RejectionReason | None = None,
        edit: PlanEditRequest | None = None,
    ) -> Turn:
        if decision not in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EDITED,
        ):
            raise NotExpectedError("a decision is either APPROVED, REJECTED or EDITED")
        if (decision is ApprovalStatus.EDITED) != (edit is not None):
            raise NotExpectedError(
                "an edit goes with an EDITED decision, and only with it"
            )
        if edit is not None and not (edit.remove or edit.add):
            raise NotExpectedError("an edit needs at least one stop to remove or add")
        thread = self._thread(thread_id)
        with thread.lock:
            self._require_waiting(thread, APPROVAL_INTERRUPT_KIND)
            if proposal_id != thread.proposal_id:
                raise StaleProposalError(
                    f"proposal {proposal_id} is not the one waiting for a decision"
                )
            resume: dict[str, Any] = {"decision": decision.value}
            if rejection_reason is not None:
                resume["rejection_reason"] = rejection_reason.value
            if edit is not None:
                resume["edit"] = {"remove": list(edit.remove), "add": list(edit.add)}
            return self._answer(thread_id, thread, thread.owner, resume)

    def confirm_events(self, thread_id: str, confirmed: bool) -> Turn:
        thread = self._thread(thread_id)
        with thread.lock:
            self._require_waiting(thread, EVENT_CONFIRMATION_INTERRUPT_KIND)
            return self._answer(
                thread_id, thread, "replanning", {"confirmed": confirmed}
            )

    def provisional(self, thread_id: str) -> Turn:
        thread = self._thread(thread_id)
        with thread.lock:
            self._require_nothing_pending(thread)
            config: Any = {"configurable": {"thread_id": thread_id}}
            values = self._graphs["initial"].get_state(config).values or {}
            constraints = values.get("constraints")
            if constraints is None or not values.get("constraints_valid"):
                raise NotExpectedError("there is no confirmed request to preview")
            lang = as_language(values.get("language"), fallback=thread.language)
            preview = self._provisional.execute(
                thread_id=thread_id,
                constraints=constraints,
                profiles=values.get("guest_profiles") or [],
                accessibility_ref=values.get("accessibility_ref") or [],
                now=self._clock(),
                lang=lang,
            )
            if preview.plan is not None:
                thread.provisional_opens = preview.opens_at
            return Turn(
                thread_id,
                TurnStatus.DONE,
                messages=(preview.text,),
                provisional_plan=preview.plan,
                provisional_opens=preview.opens_at
                if preview.plan is not None
                else None,
            )

    def refresh_provisional(self, thread_id: str) -> Turn:
        thread = self._thread(thread_id)
        with thread.lock:
            self._require_nothing_pending(thread)
            opens = thread.provisional_opens
            if opens is None:
                raise NotExpectedError("there is no provisional plan to refresh")
            now = self._clock()
            status = self._park_status.execute(now)
            if (
                status is None
                or status.closed
                or now < opens
                or now < status.opening_time
            ):
                raise NotExpectedError("the park has not opened yet")
            config: Any = {"configurable": {"thread_id": thread_id}}
            values = self._graphs["initial"].get_state(config).values or {}
            constraints = values.get("constraints")
            if constraints is None or not values.get("constraints_valid"):
                raise NotExpectedError("there is no confirmed request to plan")
            self._graphs["initial"].update_state(
                config,
                {"constraints": retimed(constraints, now.astimezone(PARK_TZ).date())},
                as_node="validate_constraints",
            )
            turn = self._run(thread_id, thread, "initial", None)
            if turn.provisional_offer:
                return turn
            thread.provisional_opens = None
            if turn.status is TurnStatus.NEEDS_APPROVAL:
                notice = text("park_opened_refresh", thread.language)
                return replace(turn, messages=(notice, *turn.messages))
            return turn

    def report_events(self, thread_id: str, events: Sequence[Event]) -> Turn:
        thread = self._thread(thread_id)
        with thread.lock:
            if thread.has_unapplied:
                raise NotExpectedError(
                    "the last answer is still being applied; retry it first"
                )
            if thread.waiting_for not in (None, APPROVAL_INTERRUPT_KIND):
                raise NotExpectedError(
                    f"the planner is waiting for {thread.waiting_for}"
                )
            self._require_known_attractions(events, thread.language)
            payload = {"thread_id": thread_id, "events": list(events)}
            return self._run(thread_id, thread, "replanning", payload)

    def _require_known_attractions(
        self, events: Sequence[Event], lang: Language
    ) -> None:
        named = {e.attraction_id for e in events if e.attraction_id is not None}
        if not named:
            return
        with open_deps(self._deps_factory) as deps:
            catalog = load_catalog(deps)
        unknown = sorted(named - {a.node_id for a in catalog})
        if unknown:
            groups = "; ".join(
                f"{text(f'attraction_group_{category}', lang)} ({', '.join(names)})"
                for category, names in suggest_attractions(catalog).items()
            )
            options = text("attraction_options", lang, groups=groups) if groups else ""
            raise UnknownAttractionError(
                text(
                    "event_unknown_attraction",
                    lang,
                    name=", ".join(unknown),
                    options=options,
                )
            )

    def pending_proposal(self, thread_id: str) -> tuple[Proposal, Plan] | None:
        self._thread(thread_id)
        pending = self._pending.execute(thread_id)
        return None if pending is None else (pending.proposal, pending.candidate_plan)

    def active_plan(self, thread_id: str) -> Plan | None:
        self._thread(thread_id)
        return self._active.execute(thread_id)

    def _with_closed_notice(self, thread: _Thread, turn: Turn) -> Turn:
        """The first reply of a closed day says so, instead of only asking about the party."""
        if turn.status is not TurnStatus.NEEDS_ANSWER:
            return turn
        status = self._park_status.execute(self._clock())
        if status is None or not status.closed:
            return turn
        notice = text(
            "park_closed_heads_up", thread.language, closing=status.closing_label()
        )
        return replace(turn, messages=(notice, *turn.messages))

    def _thread(self, thread_id: str) -> _Thread:
        with self._registry_lock:
            thread = self._threads.get(thread_id)
        if thread is None:
            raise UnknownThreadError(f"unknown session {thread_id!r}")
        return thread

    @staticmethod
    def _require_nothing_pending(thread: _Thread) -> None:
        if thread.waiting_for is not None:
            raise NotExpectedError(f"the planner is waiting for {thread.waiting_for}")

    @staticmethod
    def _require_waiting(thread: _Thread, kind: str) -> None:
        if thread.waiting_for != kind:
            waiting = thread.waiting_for or "nothing"
            raise NotExpectedError(f"the planner is waiting for {waiting}, not {kind}")

    def _answer(
        self, thread_id: str, thread: _Thread, owner: Owner, value: Any
    ) -> Turn:
        """Answer the interrupt the thread is paused on, by its id.

        A plain ``Command(resume=value)`` kept by a failed run would, inside the planning
        subgraph, also answer the next interrupt of the same run. So the answer names its
        interrupt, and after a failure the same answer retries the run with no input:
        LangGraph already holds it. A different answer is refused until the retry succeeds.
        """
        if thread.has_unapplied:
            if value != thread.unapplied:
                raise NotExpectedError(
                    "the last answer is still being applied; send the same answer again to retry"
                )
            graph_input: Any = None
        else:
            graph_input = Command(resume={thread.interrupt_id: value})
        try:
            turn = self._run(thread_id, thread, owner, graph_input)
        except Exception:
            thread.unapplied, thread.has_unapplied = value, True
            raise
        thread.unapplied, thread.has_unapplied = None, False
        return turn

    def _run(
        self, thread_id: str, thread: _Thread, owner: Owner, graph_input: Any
    ) -> Turn:
        config: Any = {"configurable": {"thread_id": thread_id}}
        if self._callbacks:
            config["callbacks"] = list(self._callbacks)
            config["metadata"] = {
                "langfuse_session_id": thread_id,
                "langfuse_tags": [owner],
            }
        try:
            result = self._graphs[owner].invoke(graph_input, config=config)
        except PlanningUnavailableError as exc:
            logger.exception("planning is unavailable")
            state: Any = self._graphs[owner].get_state(config).values or {}
            message = text("unavailable", thread.language, solo=solo_of(state))
            raise ServiceUnavailableError(message) from exc
        thread.owner = owner
        return self._turn(thread_id, thread, result)

    def _turn(self, thread_id: str, thread: _Thread, result: dict[str, Any]) -> Turn:
        everything = list(result.get("messages") or [])
        fresh = everything[thread.seen_messages :]
        thread.seen_messages = len(everything)
        texts = tuple(
            str(m.content) for m in fresh if isinstance(m, AIMessage) and m.content
        )
        interrupts = result.get("__interrupt__")
        if not interrupts:
            thread.waiting_for = None
            thread.proposal_id = None
            thread.interrupt_id = None
            return Turn(
                thread_id,
                TurnStatus.DONE,
                messages=texts,
                provisional_offer=bool(result.get("provisional_offer")),
            )

        payload = interrupts[0].value
        kind = payload["kind"]
        thread.interrupt_id = interrupts[0].id
        thread.waiting_for = kind
        thread.proposal_id = None
        error = payload.get("error")
        if kind == MISSING_INFORMATION_KIND:
            question = payload.get("question")
            return Turn(
                thread_id,
                TurnStatus.NEEDS_ANSWER,
                messages=tuple(t for t in texts if t != question),
                missing=tuple(payload.get("missing") or ()),
                question=question,
            )
        if kind == HARD_CONSTRAINT_KIND:
            request = ConfirmationRequest(
                pending=tuple(payload["pending"]),
                echo=tuple(payload["echo"]),
                accessibility_guests=tuple(payload["accessibility_guests"]),
                consent_required=payload.get("reason") == "consent_required",
            )
            return Turn(
                thread_id,
                TurnStatus.NEEDS_CONFIRMATION,
                messages=texts,
                confirmation=request,
            )
        if kind == APPROVAL_INTERRUPT_KIND:
            proposal: Proposal = payload["proposal"]
            thread.proposal_id = proposal.proposal_id
            return Turn(
                thread_id,
                TurnStatus.NEEDS_APPROVAL,
                messages=texts,
                proposal=proposal,
                candidate_plan=payload["candidate_plan"],
                error=error,
            )
        if kind == EVENT_CONFIRMATION_INTERRUPT_KIND:
            return Turn(
                thread_id,
                TurnStatus.NEEDS_EVENT_CONFIRMATION,
                messages=texts,
                held_events=tuple(payload["events"]),
                error=error,
            )
        raise RuntimeError(f"unknown interrupt kind {kind!r}")
