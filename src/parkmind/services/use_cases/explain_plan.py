"""Use case: EXPLAIN a plan that already passed the ConstraintChecker.

Explanation happens only after validation: ``execute`` raises
``ExplainBeforeValidationError`` for a result that is not valid. The P0 text is a
deterministic template of the plan (the LLM concierge can replace it later behind
this use case); it reads the plan and never feeds anything back into it.
"""

from collections.abc import Mapping

from parkmind.core.contracts import PARK_TZ, CheckResult, Plan
from parkmind.services.planning.optimizer import MEAL_VENUE_TBD
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    open_deps,
)


class ExplainBeforeValidationError(RuntimeError):
    """An explanation was requested for a plan the checker has not passed."""


def explain_plan(plan: Plan, check_result: CheckResult, names: Mapping[str, str]) -> str:
    if not check_result.valid:
        raise ExplainBeforeValidationError("a plan is explained only after it passed the checker")
    names = {**names, MEAL_VENUE_TBD: "Lunch (restaurant to be chosen, near your previous stop)"}
    lines = [
        f"{stop.arrival_time.astimezone(PARK_TZ):%H:%M} "
        f"{names.get(stop.node_id, stop.node_id)} "
        f"({stop.expected_wait_minutes:.0f} min wait, {stop.walking_minutes:.0f} min walk)"
        for stop in plan.stops
    ]
    summary = (
        f"{len(plan.stops)} stops, {plan.total_wait_minutes:.0f} min of waiting and "
        f"{plan.total_walking_minutes:.0f} min of walking in total."
    )
    parts = [summary, *lines]
    if plan.unmet_must_do:
        unmet = ", ".join(names.get(node_id, node_id) for node_id in plan.unmet_must_do)
        parts.append(f"Could not fit your must-dos today: {unmet}.")
    return "\n".join(parts)


class ExplainPlanUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(self, plan: Plan, check_result: CheckResult) -> str:
        if not check_result.valid:
            raise ExplainBeforeValidationError(
                "a plan is explained only after it passed the checker"
            )
        with open_deps(self._deps_factory) as deps:
            names = {a.node_id: a.name for a in load_catalog(deps)}
        return explain_plan(plan, check_result, names)
