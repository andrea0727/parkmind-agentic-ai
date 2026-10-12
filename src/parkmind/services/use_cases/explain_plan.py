"""Use case: EXPLAIN a plan that already passed the ConstraintChecker.

Explanation happens only after validation: ``execute`` raises
``ExplainBeforeValidationError`` for a result that is not valid. The P0 text is a
deterministic template of the plan (the LLM concierge can replace it later behind
this use case); it reads the plan and never feeds anything back into it.
"""

from collections.abc import Mapping

from parkmind.core.contracts import PARK_TZ, CheckResult, Plan, ExplainRequest, PlanExplanation
from parkmind.services.explanation.generator import PlanExplanationGenerator
from parkmind.services.planning.optimizer import MEAL_VENUE_TBD



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
    """Use case to generate grounded explanations for plans."""

    def __init__(self, generator: PlanExplanationGenerator | None = None) -> None:
        self._generator = generator or PlanExplanationGenerator()

    def execute(self, request: ExplainRequest) -> PlanExplanation:
        """Execute the explanation generation for a given ExplainRequest."""
        return self._generator.generate(request)