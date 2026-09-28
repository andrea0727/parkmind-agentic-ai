"""Use case: persist a candidate plan and open a PENDING proposal for it.

Wraps PlanRepository.save + ProposalRepository.save in one transaction so
agents/graph never import services.clients directly (see .importlinter
boundary contract). Required before a plan can ever be activated: activate()
only succeeds against an APPROVED proposal for it (section 23).
"""

from parkmind.core.contracts import ApprovalStatus, Plan, PlanDiff, Proposal
from parkmind.services.clients.postgres import (
    PostgresPlanRepository,
    PostgresProposalRepository,
    connect,
)


class ProposePlanUseCase:
    def execute(
        self,
        thread_id: str,
        plan: Plan,
        *,
        proposal_id: str,
        reason: str,
        explanation: str,
        base_plan_id: str | None = None,
    ) -> Proposal:
        """Persist ``plan`` and open a PENDING proposal for human review.

        ``base_plan_id`` defaults to the candidate's own id: a thread's first
        proposal has no prior plan to diff against (the proposals table keeps
        base_plan_id NOT NULL but without an FK precisely for this case --
        see database/migrations/versions/0001_mvp_schema.py).
        """
        proposal = Proposal(
            proposal_id=proposal_id,
            base_plan_id=base_plan_id or plan.plan_id,
            candidate_plan_id=plan.plan_id,
            reason=reason,
            diff=PlanDiff(),
            explanation=explanation,
            approval_status=ApprovalStatus.PENDING,
            provenance=plan.provenance,
        )
        with connect() as conn, conn.transaction():
            PostgresPlanRepository(conn).save(thread_id, plan)
            PostgresProposalRepository(conn).save(thread_id, proposal)
        return proposal
