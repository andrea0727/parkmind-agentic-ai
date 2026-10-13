"""EXPLAIN runs only for a plan that passed the checker."""

import factories
import pytest
from planning_support import PlanningAttractionRepository, factory_for, make_deps

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


# --- who sits out a height-restricted ride ---------------------------------------

ADULT = factories.guest(guest_id="g1", height_cm=175)
CHILD = factories.guest(guest_id="g2", role="child", height_cm=90)


def _restricted_plan(served: list[str]):
    return factories.plan(stops=[factories.stop(node_id="a1", served_guests=served)])


def test_a_guest_too_short_for_a_ride_is_named_as_skipping_it():
    text = explain_plan(
        _restricted_plan(["g1"]),
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        guests=[ADULT, CHILD],
        height_limits={"a1": 112},
    )

    assert "Guest 2 (90 cm) will skip Space Mountain (minimum height 112 cm)" in text
    assert "Guest 1" not in text


def test_nothing_is_said_when_everyone_rides():
    text = explain_plan(
        _restricted_plan(["g1", "g2"]),
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        guests=[ADULT, factories.guest(guest_id="g2", height_cm=130)],
        height_limits={"a1": 112},
    )

    assert "skip" not in text


def test_a_whole_party_stop_or_an_unrestricted_ride_adds_no_line():
    names = {"a1": "Space Mountain"}
    whole_party = explain_plan(
        _restricted_plan([]),
        CheckResult(valid=True),
        names,
        guests=[ADULT, CHILD],
        height_limits={"a1": 112},
    )
    unrestricted = explain_plan(
        _restricted_plan(["g1"]), CheckResult(valid=True), names, guests=[ADULT, CHILD]
    )

    assert "skip" not in whole_party and "skip" not in unrestricted


def test_a_guest_with_unknown_height_who_does_not_ride_is_named_too():
    unknown = factories.guest(guest_id="g2", height_cm=None)

    text = explain_plan(
        _restricted_plan(["g1"]),
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        guests=[ADULT, unknown],
        height_limits={"a1": 112},
    )

    assert "Guest 2 will skip Space Mountain" in text


def test_the_use_case_reads_the_height_limits_from_the_catalog():
    plan = _restricted_plan(["g1"])
    catalog = PlanningAttractionRepository([factories.attraction(height_restriction_cm=112)])

    text = ExplainPlanUseCase(factory_for(make_deps(attractions=catalog))).execute(
        plan, CheckResult(valid=True), guests=[ADULT, CHILD]
    )

    assert "Guest 2 (90 cm) will skip Space Mountain (minimum height 112 cm)" in text
