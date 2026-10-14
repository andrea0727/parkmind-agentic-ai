"""The offline metrics pass on faithful output and fail, with a reason, on each kind of corruption."""

import pytest

pytest.importorskip("deepeval")

from evals import eval_scenarios
from evals.eval_metrics import (
    ConstraintPreservationMetric,
    ConstraintSnapshot,
    GroundednessMetric,
    constraint_test_case,
    explanation_test_case,
)
from evals.eval_thresholds import ACCURACY, GROUNDEDNESS

PAYLOAD_JSON = eval_scenarios.payload().to_json()


def grounded(explanation: str) -> tuple[GroundednessMetric, float]:
    metric = GroundednessMetric(eval_scenarios.known_attractions())
    score = metric.measure(explanation_test_case(explanation, PAYLOAD_JSON))
    return metric, score


def test_an_explanation_made_only_of_plan_facts_is_grounded() -> None:
    metric, score = grounded(eval_scenarios.GOOD_EXPLANATION)
    assert score == 1.0
    assert metric.is_successful()


def test_an_invented_47_minute_wait_is_not_grounded() -> None:
    bad = eval_scenarios.GOOD_EXPLANATION.replace("20 minute wait", "47 minute wait")
    metric, score = grounded(bad)
    assert score == 0.0
    assert not metric.is_successful()
    assert "47" in str(metric.reason)


def test_an_invented_arrival_time_is_not_grounded() -> None:
    bad = eval_scenarios.GOOD_EXPLANATION.replace("10:40", "10:55")
    metric, _ = grounded(bad)
    assert not metric.is_successful()
    assert "10:55" in str(metric.reason)


def test_an_attraction_missing_from_the_plan_is_not_grounded() -> None:
    bad = (
        eval_scenarios.GOOD_EXPLANATION + " Afterwards you could ride Haunted Mansion."
    )
    metric, _ = grounded(bad)
    assert not metric.is_successful()
    assert "Haunted Mansion" in str(metric.reason)


def test_a_plan_attraction_is_not_reported_as_invented() -> None:
    metric, _ = grounded("Space Mountain first, then Big Thunder Mountain.")
    assert metric.is_successful()


def test_an_attraction_name_inside_a_longer_word_is_not_a_mention() -> None:
    metric, _ = grounded("Your Carouselling day starts at 10:00.")
    assert metric.is_successful()


def test_groundedness_is_judged_in_the_language_of_the_explanation() -> None:
    metric = GroundednessMetric(eval_scenarios.known_attractions(), language="es")
    faithful = "Empiezas en Space Mountain a las 10:00 con 20 minutos de espera."
    invented = "Empiezas en Space Mountain a las 10:00 con 47 minutos de espera."
    metric.measure(explanation_test_case(faithful, PAYLOAD_JSON))
    assert metric.is_successful()
    metric.measure(explanation_test_case(invented, PAYLOAD_JSON))
    assert not metric.is_successful()


def test_groundedness_uses_the_shared_threshold() -> None:
    assert GroundednessMetric([]).threshold == GROUNDEDNESS


def test_metrics_carry_readable_names_in_reports() -> None:
    assert GroundednessMetric([]).__name__ == "Groundedness"
    assert ConstraintPreservationMetric().__name__ == "Constraint Preservation"


EXPECTED = ConstraintSnapshot(
    party_size=3,
    heights_cm=[None, 122, 100],
    departure_time="18:00",
    must_do=["Space Mountain", "TRON"],
    avoid=["Splash Mountain"],
)


def preserved(actual: ConstraintSnapshot) -> ConstraintPreservationMetric:
    metric = ConstraintPreservationMetric()
    metric.measure(constraint_test_case(actual, EXPECTED))
    return metric


def test_constraints_equal_to_the_label_are_preserved() -> None:
    metric = preserved(EXPECTED.model_copy())
    assert metric.score == 1.0
    assert metric.is_successful()


def test_must_do_names_match_regardless_of_case_and_order() -> None:
    reordered = EXPECTED.model_copy(update={"must_do": ["tron", "SPACE  mountain"]})
    assert preserved(reordered).is_successful()


def test_a_changed_height_fails_the_metric() -> None:
    changed = EXPECTED.model_copy(update={"heights_cm": [None, 122, 200]})
    metric = preserved(changed)
    assert not metric.is_successful()
    assert metric.score == pytest.approx(0.8)
    assert "heights" in str(metric.reason)


def test_a_height_changed_from_400_to_200_fails_the_metric() -> None:
    expected = EXPECTED.model_copy(update={"heights_cm": [None, 400, 100]})
    metric = ConstraintPreservationMetric()
    metric.measure(
        constraint_test_case(
            expected.model_copy(update={"heights_cm": [None, 200, 100]}), expected
        )
    )
    assert not metric.is_successful()


def test_a_dropped_must_do_fails_the_metric() -> None:
    dropped = EXPECTED.model_copy(update={"must_do": ["TRON"]})
    metric = preserved(dropped)
    assert not metric.is_successful()
    assert "dropped ['space mountain']" in str(metric.reason)


def test_an_invented_avoid_fails_the_metric() -> None:
    invented = EXPECTED.model_copy(update={"avoid": ["Splash Mountain", "TRON"]})
    metric = preserved(invented)
    assert not metric.is_successful()
    assert "invented ['tron']" in str(metric.reason)


def test_a_wrong_departure_and_party_size_each_lower_the_score() -> None:
    wrong = EXPECTED.model_copy(update={"departure_time": "19:00", "party_size": 4})
    metric = preserved(wrong)
    assert metric.score == pytest.approx(0.6)
    assert not metric.is_successful()


def test_preservation_uses_the_shared_threshold() -> None:
    assert ConstraintPreservationMetric().threshold == ACCURACY
