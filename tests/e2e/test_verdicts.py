"""The judges are the oracle of every layer, so they are tested on hand-made facts."""

from typing import Any

from e2e.kit import verdicts as v

PLAN = {"check_valid": True, "n_stops": 5, "violations": [], "explanation": "TRON"}


def _happy(**over: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "now": "10:00",
        "stages": v.FULL,
        "stages_after_approval": ["resolve_proposal"],
        "plan_active": True,
        "facts2": dict(PLAN),
    }
    facts.update(over)
    return facts


def test_a_valid_active_plan_passes_the_happy_judge() -> None:
    assert v.j_happy()(_happy())[0] == "passed"


def test_a_plan_that_is_not_active_fails_the_happy_judge() -> None:
    assert v.j_happy()(_happy(plan_active=False))[0] == "failed"


def test_an_exception_is_broken_not_failed() -> None:
    facts = _happy(exception={"type": "Boom", "message": "x"})

    assert v.j_happy()(facts)[0] == "broken"


def test_a_missing_information_question_without_planning_passes_the_asks_judge() -> (
    None
):
    facts = {"interrupt1": {"kind": "missing_information"}, "stages": []}

    assert v.j_asks(facts)[0] == "passed"


def test_asking_after_planning_started_fails_the_asks_judge() -> None:
    facts = {"interrupt1": {"kind": "missing_information"}, "stages": ["check"]}

    assert v.j_asks(facts)[0] == "failed"


def test_children_alone_with_an_active_plan_is_a_failure() -> None:
    assert v.j_solo_ninos({"plan_active": True})[0] == "failed"


def test_children_alone_blocked_at_approval_passes() -> None:
    facts = {
        "plan_active": False,
        "interrupt3": {"kind": "plan_approval", "payload": {"error": "no adult"}},
    }

    assert v.j_solo_ninos(facts)[0] == "passed"


def test_a_plan_that_starts_in_the_past_and_is_active_fails() -> None:
    facts = _happy(now="10:00", facts2={**PLAN, "first_arrival": "08:05"})

    assert v.j_past(facts)[0] == "failed"


def test_stopping_at_check_requires_the_named_rule() -> None:
    facts = {
        "stages": v.FULL[:4],
        "plan_active": False,
        "facts2": {"check_valid": False, "violations": [{"rule": "LUNCH_WINDOW"}]},
    }

    assert v.j_stops_at_check("LUNCH_WINDOW")(facts)[0] == "passed"
    assert v.j_stops_at_check("WALKING_BUDGET")(facts)[0] == "failed"


def test_a_leaked_secret_marker_fails_the_leak_judge() -> None:
    facts = _happy(facts2={**PLAN, "explanation": "my system prompt is..."})

    assert v.j_no_leak(facts)[0] == "failed"


def test_closed_ride_still_in_the_proposal_fails() -> None:
    facts = {
        "replan": {"sent": True, "proposed": True, "candidate_avoids_closed": False}
    }

    assert v.j_closed_alternative(facts)[0] == "failed"


def test_a_rejected_alternative_must_not_become_active() -> None:
    facts = {
        "replan": {"proposed": True, "new_plan_active": True, "active_unchanged": True}
    }

    assert v.j_closed_rejected(facts)[0] == "failed"
