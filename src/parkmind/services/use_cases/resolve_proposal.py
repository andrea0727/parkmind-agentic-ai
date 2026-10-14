"""Use case: resolve a proposal and, only if approved, activate its plan.

Resolves the proposal and activates the plan in one transaction
(``PlanningDeps.transaction``) so agents/graph never import services.clients
directly (see .importlinter boundary contract). This is the only call site for
PlanRepository.activate: nothing in the LLM/agent path may activate a plan, only
the human-approval resume path (see graph/initial_planning_graph.py,
services/ports/plan_repository.py).

Approving a replan also carries the progress *reported* for the base plan
(completed or skipped stops, walked minutes) over to the new plan id, read at
approval so what the guests reported while deciding is kept. Progress that was
only assumed (an on-schedule party, see ``effective_execution_state``) is never
stored: it is derived again from the new plan and the clock on the next replan.
A rejected or superseded proposal leaves no state behind.

A proposal can stop being PENDING while the guests are still deciding: a
significant event supersedes it (section 9 [C18]). Deciding on it then
raises ``ProposalNoLongerPendingError`` and changes nothing.

Every decision writes one ``BehaviorEntry`` per guest of the party, in the same
transaction (section 23: "behavior log: ACCEPTED / EDITED / REJECTED"). The log is
keyed by guest, and a party's guest ids (``g1``, ``g2``) are only unique within its
thread, so each guest is registered as ``party_guest_id(thread_id, guest_id)`` --
with its role only, never its height (section 12). ``entry_id`` is the
proposal and the decision, so a decision applied twice is logged once; a
superseded proposal logs nothing. An edit's entry also records, apart, what the
edit removed and what it added: for learning they are opposite signals.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from parkmind.core.contracts import (
    ApprovalStatus,
    BehaviorEntry,
    BehaviorEventType,
    Guest,
    Proposal,
    RejectionReason,
)
from parkmind.services.ports import InvalidStateTransitionError, party_guest_id
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    open_deps,
)

_LOGGED: dict[
    ApprovalStatus,
    tuple[BehaviorEventType, Literal["ACCEPTED", "REJECTED", "EDITED"]],
] = {
    ApprovalStatus.APPROVED: (BehaviorEventType.PROPOSAL_ACCEPTED, "ACCEPTED"),
    ApprovalStatus.REJECTED: (BehaviorEventType.PROPOSAL_REJECTED, "REJECTED"),
    ApprovalStatus.EDITED: (BehaviorEventType.PROPOSAL_EDITED, "EDITED"),
}


class ProposalNoLongerPendingError(RuntimeError):
    """The proposal was already resolved or superseded; the decision was not applied."""

    def __init__(self, proposal_id: str, status: ApprovalStatus | None) -> None:
        self.proposal_id = proposal_id
        self.status = status
        label = status.value if status is not None else "unknown"
        super().__init__(f"proposal {proposal_id} is {label}, not PENDING")


class ResolveProposalUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(
        self,
        thread_id: str,
        proposal_id: str,
        status: ApprovalStatus,
        *,
        at: datetime,
        guests: Sequence[Guest],
        rejection_reason: RejectionReason | None = None,
        attraction_ids: Sequence[str] = (),
        removed_attraction_ids: Sequence[str] = (),
        added_attraction_ids: Sequence[str] = (),
    ) -> Proposal:
        """``guests`` is the party (at least one: every decision is logged per guest);
        ``attraction_ids`` are the attractions of the plan decided on (section 23).
        An edit must say what it removed and/or added; no other decision may."""
        if status not in _LOGGED:
            raise ValueError(f"{status.value} is not a decision")
        if not guests:
            raise ValueError("a decision is logged per guest: the party's guests are required")
        edited = status is ApprovalStatus.EDITED
        if edited and not (removed_attraction_ids or added_attraction_ids):
            raise ValueError("an edit is logged with what it removed and/or added")
        if not edited and (removed_attraction_ids or added_attraction_ids):
            raise ValueError("only an edit removes or adds attractions")
        with open_deps(self._deps_factory) as deps, deps.transaction():
            stored = deps.proposals.get(proposal_id)
            if (
                stored is not None
                and stored.approval_status is not ApprovalStatus.PENDING
            ):
                raise ProposalNoLongerPendingError(proposal_id, stored.approval_status)
            try:
                proposal = deps.proposals.resolve(
                    proposal_id, status, at=at, rejection_reason=rejection_reason
                )
            except InvalidStateTransitionError as exc:  # superseded meanwhile
                current = deps.proposals.get(proposal_id)
                raise ProposalNoLongerPendingError(
                    proposal_id, current.approval_status if current else None
                ) from exc
            if status is ApprovalStatus.APPROVED:
                deps.plans.activate(thread_id, proposal.candidate_plan_id, at=at)
                if proposal.base_plan_id != proposal.candidate_plan_id:
                    reported = deps.execution_states.get(proposal.base_plan_id)
                    if reported is not None:
                        deps.execution_states.save(
                            reported.model_copy(
                                update={"plan_id": proposal.candidate_plan_id}
                            )
                        )
            event_type, decision = _LOGGED[status]
            entry = BehaviorEntry(
                entry_id=f"{proposal_id}:{decision}",
                event_type=event_type,
                proposal_id=proposal_id,
                plan_id=proposal.candidate_plan_id,
                decision=decision,
                rejection_reason=rejection_reason,
                attraction_ids=list(attraction_ids),
                removed_attraction_ids=list(removed_attraction_ids),
                added_attraction_ids=list(added_attraction_ids),
                timestamp=at,
            )
            for guest in guests:
                scoped = party_guest_id(thread_id, guest.guest_id)
                deps.guests.save(Guest(guest_id=scoped, role=guest.role))
                deps.behavior.append(scoped, entry)
        return proposal
