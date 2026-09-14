"""Baseline 2: run the real optimizer once at the start of the day and
never replan, regardless of events. Reuses services.planning.optimizer."""


class StaticPlannerBaseline:
    def build_plan(self, constraints, context):
        raise NotImplementedError
