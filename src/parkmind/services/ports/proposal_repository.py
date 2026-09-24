"""ProposalRepository -- candidate proposals awaiting a human (section 22-23, 33 [C18]).

A thread holds at most one PENDING proposal: a concurrent event supersedes the
pending one and replanning restarts from the active plan (section 43 [C18]).
A proposal only ever leaves PENDING; APPROVED / REJECTED / EDITED / SUPERSEDED
are final (an edit produces a *new* Proposal, section 23).
"""

from datetime import datetime
from typing import Protocol

from parkmind.core.contracts import ApprovalStatus, Proposal, RejectionReason


class ProposalRepository(Protocol):
    def save(self, thread_id: str, proposal: Proposal) -> None:
        """Store ``proposal`` (and its ``Provenance``).

        A proposal is created PENDING: any other status raises
        ``InvalidStateTransitionError``, so a decision can only be recorded by
        ``resolve`` and a plan can never be activated without one (section 23).
        Raises ``NotFoundError`` if its candidate plan was not saved first and
        ``PendingProposalExistsError`` if it is PENDING while the thread
        already holds another PENDING proposal. Re-saving a proposal with the
        same body is a no-op and never resets a resolution; a different body
        under the same ``proposal_id`` raises ``ProposalImmutableError``.
        """
        ...

    def get(self, proposal_id: str) -> Proposal | None: ...

    def list_pending(self, thread_id: str) -> list[Proposal]: ...

    def resolve(
        self,
        proposal_id: str,
        status: ApprovalStatus,
        *,
        at: datetime,
        rejection_reason: RejectionReason | None = None,
    ) -> Proposal:
        """Move a PENDING proposal to ``status`` and return it.

        Raises ``NotFoundError`` for an unknown proposal and
        ``InvalidStateTransitionError`` if it is not PENDING or ``status`` is
        PENDING.
        """
        ...

    def supersede_pending(self, thread_id: str, *, at: datetime) -> list[str]:
        """Mark every PENDING proposal of the thread SUPERSEDED; return their ids."""
        ...
