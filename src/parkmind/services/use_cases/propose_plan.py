"""Use case: persist a candidate plan and open a PENDING proposal for it.

Saves the plan and the proposal in one transaction (``PlanningDeps.transaction``)
so agents/graph never import services.clients directly (see .importlinter
boundary contract). Required before a plan can ever be activated: activate()
only succeeds against an APPROVED proposal for it (section 23).

A thread has one PENDING proposal at a time: opening a new one supersedes the
earlier ones, so a re-run of the planning stage never leaves two open.
"""

from datetime import datetime

from parkmind.core.contracts import ApprovalStatus, Plan, PlanDiff, Proposal
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    open_deps,
)


class ProposePlanUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(
        self,
        thread_id: str,
        plan: Plan,
        *,
        proposal_id: str,
        reason: str,
        explanation: str,
        at: datetime,
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
        with open_deps(self._deps_factory) as deps, deps.transaction():
            deps.proposals.supersede_pending(thread_id, at=at)
            deps.plans.save(thread_id, plan)
            deps.proposals.save(thread_id, proposal)
        return proposal
