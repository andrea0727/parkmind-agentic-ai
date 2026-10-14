"""The conversation the HTTP layer talks to: one party's dialogue with the planner (P0-35).

The API never touches a graph. It holds a ``Conversation`` and passes what the guests said
or decided; the implementation (``graph/conversation.py``) runs the right graph and reports
what the planner needs next as a ``Turn``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from parkmind.core.contracts import (
    ApprovalStatus,
    Event,
    Plan,
    Proposal,
    RejectionReason,
)


class TurnStatus(str, Enum):
    NEEDS_ANSWER = "needs_answer"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_APPROVAL = "needs_approval"
    NEEDS_EVENT_CONFIRMATION = "needs_event_confirmation"
    DONE = "done"


class ConversationError(Exception):
    """Base of the errors the HTTP layer maps to a status code."""


class UnknownThreadError(ConversationError):
    """No session with this id exists in this process."""


class NotExpectedError(ConversationError):
    """The input does not answer what the planner is waiting for."""


class UnknownAttractionError(ConversationError):
    """An event names an attraction that is not in the park catalog."""


class StaleProposalError(ConversationError):
    """The decision names a proposal that is no longer the one waiting for the guests."""


class ServiceUnavailableError(Exception):
    """A service the planner needs is down; the message is safe to show the guests."""


@dataclass(frozen=True)
class ConfirmationRequest:
    """Hard constraints the guests must confirm before anything is planned."""

    pending: tuple[str, ...]
    echo: tuple[str, ...]
    accessibility_guests: tuple[str, ...]
    consent_required: bool = False


@dataclass(frozen=True)
class Turn:
    """What happened on a thread and what, if anything, the planner is waiting for."""

    thread_id: str
    status: TurnStatus
    messages: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    question: str | None = None
    confirmation: ConfirmationRequest | None = None
    proposal: Proposal | None = None
    candidate_plan: Plan | None = None
    held_events: tuple[Event, ...] = ()
    error: str | None = None
    provisional_offer: bool = False
    """The park is closed; ``Conversation.provisional`` can preview tomorrow."""
    provisional_plan: Plan | None = None
    provisional_opens: datetime | None = None
    """The preview and when the park opens: it is refreshed from then on."""


@dataclass(frozen=True)
class HardConstraintAnswer:
    confirmed: bool
    consent: bool = False
    retention_policy: str = "session_only"
    correction: str | None = None


@dataclass(frozen=True)
class PlanEditRequest:
    """An edit of a proposed plan, by node id: stops to drop and attractions to fit in."""

    remove: tuple[str, ...] = ()
    add: tuple[str, ...] = ()


class Conversation(Protocol):
    def start(self, saved_party: str | None = None) -> str:
        """A new session; its id is the ``thread_id`` of every later call.

        ``saved_party`` is the account's known party, given to the planner with the first message.
        """
        ...

    def say(self, thread_id: str, text: str, lang: str | None = None) -> Turn:
        """A guest message: the first request, the answer to a question, or a new request.

        The reply language is detected from the text; ``lang`` (the UI locale) only
        breaks the tie for a thread's first message when the text says too little.
        """
        ...

    def confirm_constraints(
        self, thread_id: str, answer: HardConstraintAnswer
    ) -> Turn: ...

    def decide(
        self,
        thread_id: str,
        proposal_id: str,
        decision: ApprovalStatus,
        rejection_reason: RejectionReason | None = None,
        edit: PlanEditRequest | None = None,
    ) -> Turn:
        """APPROVED, REJECTED or EDITED (with ``edit``).

        An edit that does not fit the plan changes nothing: the same proposal comes back
        with ``Turn.error``. A valid one comes back as a new proposal."""
        ...

    def confirm_events(self, thread_id: str, confirmed: bool) -> Turn: ...

    def provisional(self, thread_id: str) -> Turn:
        """A preview plan for the next park day, built from the confirmed request.

        Never proposed or saved; ``refresh_provisional`` replaces it once the park opens."""
        ...

    def refresh_provisional(self, thread_id: str) -> Turn:
        """Once the park has opened: plan the request again with live data.

        The result is a normal proposal for the guests to approve."""
        ...

    def report_events(self, thread_id: str, events: Sequence[Event]) -> Turn: ...

    def pending_proposal(self, thread_id: str) -> tuple[Proposal, Plan] | None:
        """The proposal waiting for the guests, with its candidate plan."""
        ...

    def active_plan(self, thread_id: str) -> Plan | None: ...
