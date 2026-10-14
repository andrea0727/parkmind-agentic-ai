"""ApplyEditUseCase: a typed edit becomes must-do/avoid constraints, never a plan (section 23)."""

from datetime import timedelta

import factories
import pytest
from planning_support import NOW, factory_for, make_deps
from pydantic import ValidationError

from parkmind.core.contracts import StopKind
from parkmind.services.use_cases.apply_edit import (
    ApplyEditUseCase,
    EditProblem,
    InvalidEditError,
    PlanEdit,
)


def _stop(node_id: str, hour: int, kind: StopKind = StopKind.ATTRACTION):
    arrival = NOW + timedelta(hours=hour)
    return factories.stop(
        node_id=node_id,
        kind=kind,
        arrival_time=arrival,
        departure_time=arrival + timedelta(minutes=30),
    )


PLAN = factories.plan(
    stops=[
        _stop("id-tron", 0),
        _stop("id-space", 1),
        _stop("meal_venue_tbd", 2, StopKind.MEAL),
    ]
)


@pytest.fixture
def use_case() -> ApplyEditUseCase:
    return ApplyEditUseCase(factory_for(make_deps()))


def test_a_removed_stop_is_avoided_and_an_added_attraction_is_a_must_do(use_case):
    constraints = factories.party_constraints(must_do=["id-tron"], avoid=["id-teacups"])

    applied = use_case.execute(
        constraints, PLAN, PlanEdit(remove=["id-tron"], add=["id-teacups"])
    )

    assert applied.constraints.must_do == ["id-teacups"]
    assert applied.constraints.avoid == ["id-tron"]
    assert applied.constraints.constraints_version == constraints.constraints_version + 1
    assert applied.changed_ids == ["id-tron", "id-teacups"]


def test_the_rest_of_the_constraints_is_kept(use_case):
    constraints = factories.party_constraints(must_do=["id-space"], avoid=["id-carousel"])

    applied = use_case.execute(constraints, PLAN, PlanEdit(remove=["id-tron"]))

    assert applied.constraints.must_do == ["id-space"]
    assert applied.constraints.avoid == ["id-carousel", "id-tron"]
    assert applied.constraints.guests == constraints.guests
    assert applied.constraints.departure_time == constraints.departure_time


def test_repeated_ids_count_once(use_case):
    applied = use_case.execute(
        factories.party_constraints(), PLAN, PlanEdit(add=["id-teacups", "id-teacups"])
    )

    assert applied.constraints.must_do == ["id-teacups"]
    assert applied.changed_ids == ["id-teacups"]


@pytest.mark.parametrize(
    ("edit", "problem"),
    [
        (PlanEdit(), EditProblem.EMPTY),
        (PlanEdit(remove=["id-tron"], add=["id-tron"]), EditProblem.REMOVED_AND_ADDED),
        (PlanEdit(remove=["id-carousel"]), EditProblem.NOT_IN_PLAN),
        (PlanEdit(remove=["meal_venue_tbd"]), EditProblem.NOT_AN_ATTRACTION_STOP),
        (PlanEdit(add=["id-unknown"]), EditProblem.UNKNOWN_ATTRACTION),
        (PlanEdit(add=["id-space"]), EditProblem.ALREADY_IN_PLAN),
    ],
)
def test_an_edit_that_does_not_fit_the_plan_is_refused(use_case, edit, problem):
    with pytest.raises(InvalidEditError) as raised:
        use_case.execute(factories.party_constraints(), PLAN, edit)
    assert raised.value.problem is problem


def test_a_stop_already_done_cannot_be_removed(use_case):
    done = factories.execution_state(completed_stop_ids=["id-tron"])

    with pytest.raises(InvalidEditError) as raised:
        use_case.execute(
            factories.party_constraints(),
            PLAN,
            PlanEdit(remove=["id-tron"]),
            execution_state=done,
        )
    assert raised.value.problem is EditProblem.ALREADY_DONE


def test_an_edit_carries_ids_only():
    """No times, waits or walks: the planner computes those, never the guests."""
    with pytest.raises(ValidationError):
        PlanEdit.model_validate({"remove": ["id-tron"], "stops": [{"wait": 0}]})


def test_an_edit_cannot_carry_a_height_and_keeps_the_guests_as_confirmed(use_case):
    with pytest.raises(ValidationError):
        PlanEdit.model_validate({"add": ["id-teacups"], "height_cm": 150})

    short = factories.guest(guest_id="g1", height_cm=95)
    constraints = factories.party_constraints(guests=[short])

    applied = use_case.execute(constraints, PLAN, PlanEdit(add=["id-teacups"]))

    assert applied.constraints.guests == [short]
