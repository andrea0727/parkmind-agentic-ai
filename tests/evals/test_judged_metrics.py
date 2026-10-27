"""Judged metrics are built to the agreed rubric offline, and scored by the judge only when live."""

import os

import pytest

pytest.importorskip("deepeval")

from deepeval.metrics import (
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    GEval,
)
from deepeval.test_case import SingleTurnParams

from evals import eval_scenarios
from evals.eval_judge import ClaudeJudge
from evals.eval_judged import (
    LANGUAGE_CRITERIA,
    SITTING_OUT_CRITERIA,
    fact_sheet_case,
    faithfulness_metric,
    language_case,
    language_metric,
    relevancy_metric,
    sitting_out_case,
    sitting_out_metric,
)
from evals.eval_thresholds import JUDGED

PAYLOAD_JSON = eval_scenarios.payload().to_json()


@pytest.fixture
def judge() -> ClaudeJudge:
    return ClaudeJudge()


def test_the_sitting_out_rubric_asks_who_skips_and_the_minimum_height(
    judge: ClaudeJudge,
) -> None:
    metric = sitting_out_metric(judge)
    assert isinstance(metric, GEval)
    assert metric.criteria == SITTING_OUT_CRITERIA
    assert "sitting_out" in SITTING_OUT_CRITERIA
    assert "minimum height" in SITTING_OUT_CRITERIA
    assert metric.evaluation_params == [
        SingleTurnParams.INPUT,
        SingleTurnParams.ACTUAL_OUTPUT,
    ]


def test_the_language_rubric_requires_the_guests_language_in_both_directions(
    judge: ClaudeJudge,
) -> None:
    metric = language_metric(judge)
    assert metric.criteria == LANGUAGE_CRITERIA
    assert "Spanish in, Spanish out" in LANGUAGE_CRITERIA
    assert metric.evaluation_params == [
        SingleTurnParams.INPUT,
        SingleTurnParams.ACTUAL_OUTPUT,
    ]


def test_every_judged_metric_uses_the_shared_threshold(judge: ClaudeJudge) -> None:
    metrics = [
        sitting_out_metric(judge),
        language_metric(judge),
        relevancy_metric(judge),
        faithfulness_metric(judge),
    ]
    assert [metric.threshold for metric in metrics] == [JUDGED] * 4


def test_every_judged_metric_scores_with_the_claude_judge_and_never_a_native_model(
    judge: ClaudeJudge,
) -> None:
    metrics = [
        sitting_out_metric(judge),
        language_metric(judge),
        relevancy_metric(judge),
        faithfulness_metric(judge),
    ]
    assert all(metric.model is judge for metric in metrics)


def test_judged_metrics_run_in_the_calling_thread(judge: ClaudeJudge) -> None:
    metrics = [
        sitting_out_metric(judge),
        language_metric(judge),
        relevancy_metric(judge),
        faithfulness_metric(judge),
    ]
    assert not any(metric.async_mode for metric in metrics)


def test_relevancy_and_faithfulness_are_the_stock_metrics(judge: ClaudeJudge) -> None:
    assert isinstance(relevancy_metric(judge), AnswerRelevancyMetric)
    assert isinstance(faithfulness_metric(judge), FaithfulnessMetric)


def test_the_fact_sheet_is_passed_to_the_judge_as_retrieval_context() -> None:
    case = fact_sheet_case(
        eval_scenarios.REQUEST, eval_scenarios.GOOD_EXPLANATION, PAYLOAD_JSON
    )
    assert case.retrieval_context == [PAYLOAD_JSON]


def test_the_sitting_out_case_shows_the_judge_the_plan_facts() -> None:
    case = sitting_out_case(PAYLOAD_JSON, eval_scenarios.GOOD_EXPLANATION)
    assert '"minimum_height_cm": 112' in str(case.input)


def test_the_language_case_pairs_the_guests_words_with_the_explanation() -> None:
    case = language_case(eval_scenarios.REQUEST_ES, eval_scenarios.GOOD_EXPLANATION_ES)
    assert case.input == eval_scenarios.REQUEST_ES


NOT_MENTIONING_THE_SKIP = "Your plan has 2 stops. You ride Space Mountain at 10:00 and Big Thunder Mountain at 10:40."
INVENTED = eval_scenarios.GOOD_EXPLANATION.replace("20 minute wait", "47 minute wait")


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
class TestWithTheJudge:
    def test_an_explanation_naming_who_sits_out_follows_the_instruction(self) -> None:
        metric = sitting_out_metric(ClaudeJudge())
        metric.measure(sitting_out_case(PAYLOAD_JSON, eval_scenarios.GOOD_EXPLANATION))
        assert metric.is_successful(), metric.reason

    def test_an_explanation_that_hides_the_skipped_ride_does_not(self) -> None:
        metric = sitting_out_metric(ClaudeJudge())
        metric.measure(sitting_out_case(PAYLOAD_JSON, NOT_MENTIONING_THE_SKIP))
        assert not metric.is_successful()

    def test_spanish_guests_get_a_spanish_explanation(self) -> None:
        metric = language_metric(ClaudeJudge())
        metric.measure(
            language_case(eval_scenarios.REQUEST_ES, eval_scenarios.GOOD_EXPLANATION_ES)
        )
        assert metric.is_successful(), metric.reason

    def test_spanish_guests_do_not_get_an_english_explanation(self) -> None:
        metric = language_metric(ClaudeJudge())
        metric.measure(
            language_case(eval_scenarios.REQUEST_ES, eval_scenarios.GOOD_EXPLANATION)
        )
        assert not metric.is_successful()

    def test_the_explanation_answers_the_request(self) -> None:
        metric = relevancy_metric(ClaudeJudge())
        metric.measure(
            fact_sheet_case(
                eval_scenarios.REQUEST, eval_scenarios.GOOD_EXPLANATION, PAYLOAD_JSON
            )
        )
        assert metric.is_successful(), metric.reason

    def test_a_grounded_explanation_is_faithful_to_the_plan(self) -> None:
        metric = faithfulness_metric(ClaudeJudge())
        metric.measure(
            fact_sheet_case(
                eval_scenarios.REQUEST, eval_scenarios.GOOD_EXPLANATION, PAYLOAD_JSON
            )
        )
        assert metric.is_successful(), metric.reason

    def test_an_explanation_with_an_invented_wait_is_not_faithful(self) -> None:
        metric = faithfulness_metric(ClaudeJudge())
        metric.measure(fact_sheet_case(eval_scenarios.REQUEST, INVENTED, PAYLOAD_JSON))
        assert not metric.is_successful()
