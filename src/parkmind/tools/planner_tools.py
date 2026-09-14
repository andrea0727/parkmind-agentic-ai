"""
planner.* tools: resolve_group_preferences, build_plan, check_plan, replan.
These call directly into services/use_cases/*.py.
"""


def resolve_group_preferences(guest_profiles: list[dict]) -> dict:
    raise NotImplementedError


def build_plan(constraints: dict, guest_profiles: list[dict], live_context: dict) -> dict:
    raise NotImplementedError


def check_plan(plan: dict, constraints: dict) -> dict:
    raise NotImplementedError


def replan(current_plan: dict, event: dict, remaining_constraints: dict, context: dict) -> dict:
    raise NotImplementedError
