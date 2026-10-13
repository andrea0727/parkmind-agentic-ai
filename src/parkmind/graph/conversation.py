"""ConversationService: the one place that knows which graph owns a thread (P0-35).

A thread starts in the initial planning graph; once a plan exists, events run the
replanning graph on the same thread, and whichever graph last stopped for a human owns the
interrupt the next answer must resume. LangGraph does not carry an interrupt from one
graph to another, so the service remembers, per thread, who is waiting and for what, and
refuses an answer that does not fit (``NotExpectedError``) instead of resuming blindly.

Calls on one thread are serialized; different threads run in parallel. State lives in this
process (``default_checkpointer`` is in-memory), which is why the API runs one worker.
"""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from parkmind.core.contracts import (
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
    StaleProposalError,
    Turn,
    TurnStatus,
    UnknownAttractionError,
    UnknownThreadError,
)
from parkmind.core.language import as_language
from parkmind.graph.initial_planning_graph import APPROVAL_INTERRUPT_KIND
from parkmind.graph.replanning_graph import EVENT_CONFIRMATION_INTERRUPT_KIND
from parkmind.services.use_cases.active_plan import ActivePlanUseCase
from parkmind.services.use_cases.pending_proposal import PendingProposalUseCase
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    open_deps,
)

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
    ) -> None:
        self._graphs: dict[Owner, Any] = {
            "initial": initial_graph,
            "replanning": replanning_graph,
        }
        self._deps_factory = deps_factory
        self._pending = PendingProposalUseCase(deps_factory)
        self._active = ActivePlanUseCase(deps_factory)
        self._new_thread_id = new_thread_id
        self._threads: dict[str, _Thread] = {}
        self._registry_lock = threading.Lock()

    def start(self) -> str:
        thread_id = self._new_thread_id()
        with self._registry_lock:
            self._threads[thread_id] = _Thread(lock=threading.RLock())
        return thread_id

    def say(self, thread_id: str, text: str, lang: str | None = None) -> Turn:
        thread = self._thread(thread_id)
        with thread.lock:
            if thread.waiting_for == MISSING_INFORMATION_KIND:
                return self._run(thread_id, thread, "initial", Command(resume=text))
            self._require_nothing_pending(thread)
            payload: dict[str, Any] = {
                "thread_id": thread_id,
                "messages": [HumanMessage(content=text)],
            }
            hinting = not thread.language_hinted
            if hinting:
                payload["language"] = as_language(lang)
            turn = self._run(thread_id, thread, "initial", payload)
            if hinting:
                thread.language_hinted = True
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
            return self._run(thread_id, thread, "initial", Command(resume=resume))

    def decide(
        self,
        thread_id: str,
        proposal_id: str,
        decision: ApprovalStatus,
        rejection_reason: RejectionReason | None = None,
    ) -> Turn:
        if decision not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise NotExpectedError("a decision is either APPROVED or REJECTED")
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
            return self._run(thread_id, thread, thread.owner, Command(resume=resume))

    def confirm_events(self, thread_id: str, confirmed: bool) -> Turn:
        thread = self._thread(thread_id)
        with thread.lock:
            self._require_waiting(thread, EVENT_CONFIRMATION_INTERRUPT_KIND)
            return self._run(
                thread_id,
                thread,
                "replanning",
                Command(resume={"confirmed": confirmed}),
            )

    def report_events(self, thread_id: str, events: Sequence[Event]) -> Turn:
        thread = self._thread(thread_id)
        with thread.lock:
            if thread.waiting_for not in (None, APPROVAL_INTERRUPT_KIND):
                raise NotExpectedError(
                    f"the planner is waiting for {thread.waiting_for}"
                )
            self._require_known_attractions(events)
            payload = {"thread_id": thread_id, "events": list(events)}
            return self._run(thread_id, thread, "replanning", payload)

    def _require_known_attractions(self, events: Sequence[Event]) -> None:
        named = {e.attraction_id for e in events if e.attraction_id is not None}
        if not named:
            return
        with open_deps(self._deps_factory) as deps:
            known = {a.node_id for a in load_catalog(deps)}
        unknown = sorted(named - known)
        if unknown:
            raise UnknownAttractionError(
                f"unknown attraction: {', '.join(unknown)}"
            )

    def pending_proposal(self, thread_id: str) -> tuple[Proposal, Plan] | None:
        self._thread(thread_id)
        pending = self._pending.execute(thread_id)
        return None if pending is None else (pending.proposal, pending.candidate_plan)

    def active_plan(self, thread_id: str) -> Plan | None:
        self._thread(thread_id)
        return self._active.execute(thread_id)

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

    def _run(
        self, thread_id: str, thread: _Thread, owner: Owner, graph_input: Any
    ) -> Turn:
        config: Any = {"configurable": {"thread_id": thread_id}}
        result = self._graphs[owner].invoke(graph_input, config=config)
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
            return Turn(thread_id, TurnStatus.DONE, messages=texts)

        payload = interrupts[0].value
        kind = payload["kind"]
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
