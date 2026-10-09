"""EXPLAIN runs only for a plan that passed the checker."""

import factories
import pytest
from planning_support import factory_for, make_deps

from parkmind.core.contracts import CheckResult, ConstraintViolation, RuleId
from parkmind.services.use_cases.explain_plan import (
    ExplainBeforeValidationError,
    ExplainPlanUseCase,
    explain_plan,
)

INVALID = CheckResult(
    valid=False,
    violations=[
        ConstraintViolation(rule=RuleId.MUST_DO, message="missing")
    ],
)


def test_explaining_an_unvalidated_plan_raises():
    with pytest.raises(ExplainBeforeValidationError):
        explain_plan(factories.plan(), INVALID, {})
    with pytest.raises(ExplainBeforeValidationError):
        ExplainPlanUseCase(factory_for(make_deps())).execute(factories.plan(), INVALID)


def test_the_explanation_lists_each_stop_with_its_name_wait_and_walk():
    text = explain_plan(factories.plan(), CheckResult(valid=True), {"a1": "Space Mountain"})

    assert "Space Mountain (20 min wait, 5 min walk)" in text
    assert text.startswith("1 stops, 20 min of waiting and 5 min of walking")


def test_unmet_must_dos_are_called_out_by_name():
    plan = factories.plan(unmet_must_do=["id-tron"])

    text = explain_plan(plan, CheckResult(valid=True), {"id-tron": "TRON"})

    assert "Could not fit your must-dos today: TRON." in text


def test_the_use_case_resolves_names_from_the_catalog():
    plan = factories.plan(stops=[factories.stop(node_id="id-tron")])

    text = ExplainPlanUseCase(factory_for(make_deps())).execute(plan, CheckResult(valid=True))

    assert "TRON" in text


def test_a_meal_without_a_restaurant_reads_as_lunch_not_as_a_placeholder_id():
    from parkmind.core.contracts import StopKind
    from parkmind.services.planning.optimizer import MEAL_VENUE_TBD

    plan = factories.plan(stops=[factories.stop(node_id=MEAL_VENUE_TBD, kind=StopKind.MEAL)])

    text = explain_plan(plan, CheckResult(valid=True), {})

    assert "Lunch" in text and MEAL_VENUE_TBD not in text
