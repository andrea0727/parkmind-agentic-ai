"""Use case: DIFF a candidate replan against the plan it would replace (section 9).

Pure: no ports. Its own use case so the graph does not import ``services.planning``.
"""

from parkmind.core.contracts import Plan, PlanDiff
from parkmind.services.planning.plan_diff import diff_plans


class DiffPlanUseCase:
    def execute(self, *, current_plan: Plan, candidate_plan: Plan) -> PlanDiff:
        return diff_plans(old_plan=current_plan, new_plan=candidate_plan)
