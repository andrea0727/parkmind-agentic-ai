"""Use case: validate a candidate plan. Thin wrapper around
ConstraintChecker — its own use case because it's exposed as its own tool."""


class CheckPlanUseCase:
    def __init__(self, constraint_checker):
        self.constraint_checker = constraint_checker

    def execute(self, plan, constraints):
        raise NotImplementedError
