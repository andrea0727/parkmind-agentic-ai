"""Use case: resolve a proposal and, only if approved, activate its plan.

Resolves the proposal and activates the plan in one transaction
(``PlanningDeps.transaction``) so agents/graph never import services.clients
directly (see .importlinter boundary contract). This is the only call site for
PlanRepository.activate: nothing in the LLM/agent path may activate a plan, only
the human-approval resume path (see graph/initial_planning_graph.py,
services/ports/plan_repository.py).
"""

from datetime import datetime

from parkmind.core.contracts import ApprovalStatus, Proposal, RejectionReason
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    open_deps,
)


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
            proposal = deps.proposals.resolve(
                proposal_id, status, at=at, rejection_reason=rejection_reason
            )
            if status is ApprovalStatus.APPROVED:
                deps.plans.activate(thread_id, proposal.candidate_plan_id, at=at)
        return proposal
