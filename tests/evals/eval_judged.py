"""Metrics scored by an LLM judge: instruction following, language, relevance and faithfulness."""

import pytest

pytest.importorskip("deepeval")

from deepeval.metrics import (
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    GEval,
)
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.eval_judge import ClaudeJudge
from evals.eval_thresholds import JUDGED

SITTING_OUT_CRITERIA = (
    "The input is the plan facts as JSON. Where a stop lists guests under sitting_out, the "
    "explanation must say which guests skip that ride and why, naming them as the facts do "
    "and giving the minimum height. It must not say a guest rides an attraction the facts "
    "say they sit out, and it must not invent a guest who sits out."
)

LANGUAGE_CRITERIA = (
    "The input is what the guests wrote. The explanation must be written entirely in the "
    "language of that input: Spanish in, Spanish out; English in, English out. Attraction "
    "names and guest labels such as 'Guest 2' may stay as they are."
)


def sitting_out_metric(judge: ClaudeJudge) -> GEval:
    return GEval(
        name="Height-restricted rides name who sits out",
        criteria=SITTING_OUT_CRITERIA,
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=JUDGED,
        async_mode=False,
    )


def language_metric(judge: ClaudeJudge) -> GEval:
    return GEval(
        name="Answers in the guests' language",
        criteria=LANGUAGE_CRITERIA,
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=JUDGED,
        async_mode=False,
    )


def relevancy_metric(judge: ClaudeJudge) -> AnswerRelevancyMetric:
    return AnswerRelevancyMetric(threshold=JUDGED, model=judge, async_mode=False)


def faithfulness_metric(judge: ClaudeJudge) -> FaithfulnessMetric:
    return FaithfulnessMetric(threshold=JUDGED, model=judge, async_mode=False)


def fact_sheet_case(request: str, explanation: str, payload_json: str) -> LLMTestCase:
    return LLMTestCase(
        input=request, actual_output=explanation, retrieval_context=[payload_json]
    )


def sitting_out_case(payload_json: str, explanation: str) -> LLMTestCase:
    return LLMTestCase(input=payload_json, actual_output=explanation)


def language_case(guests_message: str, explanation: str) -> LLMTestCase:
    return LLMTestCase(input=guests_message, actual_output=explanation)
