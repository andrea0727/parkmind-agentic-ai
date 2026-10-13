"""Use case: resolve a proposal and, only if approved, activate its plan.

Resolves the proposal and activates the plan in one transaction
(``PlanningDeps.transaction``) so agents/graph never import services.clients
directly (see .importlinter boundary contract). This is the only call site for
PlanRepository.activate: nothing in the LLM/agent path may activate a plan, only
the human-approval resume path (see graph/initial_planning_graph.py,
services/ports/plan_repository.py).

A proposal can stop being PENDING while the guests are still deciding: a
significant event supersedes it (section 9 [C18], P0-33). Deciding on it then
raises ``ProposalNoLongerPendingError`` and changes nothing.
"""

from datetime import datetime

from parkmind.core.contracts import ApprovalStatus, Proposal, RejectionReason
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
            if status is ApprovalStatus.APPROVED:
                deps.plans.activate(thread_id, proposal.candidate_plan_id, at=at)
        return proposal
