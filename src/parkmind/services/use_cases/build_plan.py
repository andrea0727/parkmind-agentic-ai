"""
Use case: given constraints + guest profiles + live context, produce a
validated candidate Plan. This is what tools/planner_tools.py and the
graph call — nothing above this layer should import
services.planning/personalization directly.
"""


class BuildPlanUseCase:
    def __init__(self, park_graph, preference_scorer, optimizer, constraint_checker,
                 group_preference_resolver, forecast_service):
        self.park_graph = park_graph
        self.preference_scorer = preference_scorer
        self.optimizer = optimizer
        self.constraint_checker = constraint_checker
        self.group_preference_resolver = group_preference_resolver
        self.forecast_service = forecast_service

    def execute(self, constraints, guest_profiles, live_context):
        raise NotImplementedError
