"""Use case: resolve a proposal and, only if approved, activate its plan.

Wraps ProposalRepository.resolve + PlanRepository.activate in one transaction
so agents/graph never import services.clients directly (see .importlinter
boundary contract). This is the only call site for PlanRepository.activate:
nothing in the LLM/agent path may activate a plan, only the human-approval
resume path (see graph/initial_planning_graph.py, services/ports/plan_repository.py).
"""

from datetime import datetime

from parkmind.core.contracts import ApprovalStatus, Proposal, RejectionReason
from parkmind.services.clients.postgres import (
    PostgresPlanRepository,
    PostgresProposalRepository,
    connect,
)


class ResolveProposalUseCase:
    def execute(
        self,
        thread_id: str,
        proposal_id: str,
        status: ApprovalStatus,
        *,
        at: datetime,
        rejection_reason: RejectionReason | None = None,
    ) -> Proposal:
        with connect() as conn, conn.transaction():
            proposal = PostgresProposalRepository(conn).resolve(
                proposal_id, status, at=at, rejection_reason=rejection_reason
            )
            if status is ApprovalStatus.APPROVED:
                PostgresPlanRepository(conn).activate(
                    thread_id, proposal.candidate_plan_id, at=at
                )
        return proposal
