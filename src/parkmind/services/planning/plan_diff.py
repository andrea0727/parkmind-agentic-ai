"""PlanDiff — detects what changed between two plans (added/removed/moved
stops), so the agent explains a replan as a delta, not a whole new plan."""

from parkmind.core.contracts import Plan, PlanDiff as PlanDiffModel


def diff_plans(old_plan: Plan, new_plan: Plan) -> PlanDiffModel:
    raise NotImplementedError
