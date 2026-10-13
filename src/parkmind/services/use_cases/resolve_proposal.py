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
significant event supersedes it (section 9 [C18], P0-33). Deciding on it then
raises ``ProposalNoLongerPendingError`` and changes nothing.
"""
from __future__ import annotations
from datetime import datetime
from typing import Sequence

from parkmind.core.contracts import (
    ApprovalStatus,
    Proposal,
    RejectionReason,
    BehaviorEventType,
    BehaviorEntry,
)
from parkmind.services.ports import InvalidStateTransitionError
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    open_deps,
)


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
        guest_ids: Sequence[str] = (),
        rejection_reason: RejectionReason | None = None,
    ) -> Proposal:
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
            
            # Solo activa si es explícitamente APPROVED
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
            
            # Registrar el evento en el comportamiento (Behavior Log)
            event_type_map = {
                ApprovalStatus.APPROVED: BehaviorEventType.PROPOSAL_ACCEPTED,
                ApprovalStatus.REJECTED: BehaviorEventType.PROPOSAL_REJECTED,
                ApprovalStatus.EDITED: BehaviorEventType.PROPOSAL_EDITED,
            }
            event_type = event_type_map[status]

            # Si no se pasan guest_ids explícitos, usar thread_id como id de sujeto por defecto
            target_guests = guest_ids if guest_ids else [thread_id]

            for guest_id in target_guests:
                entry = BehaviorEntry(
                    entry_id=f"beh_{proposal_id}_{status.value}_{guest_id}",
                    event_type=event_type,
                    proposal_id=proposal_id,
                    plan_id=proposal.candidate_plan_id,
                    decision=status.value,
                    rejection_reason=rejection_reason,
                    timestamp=at,
                )
                deps.behavior.append(guest_id, entry)

        return proposal