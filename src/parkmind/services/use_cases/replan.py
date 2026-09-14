"""Use case: given a current plan + an event, produce a new candidate plan
and its diff against the old one."""


class ReplanUseCase:
    def __init__(self, replanner, constraint_checker, plan_diff_fn):
        self.replanner = replanner
        self.constraint_checker = constraint_checker
        self.plan_diff_fn = plan_diff_fn

    def execute(self, current_plan, event, remaining_constraints, context):
        raise NotImplementedError
