"""Naturalness of the assistant's question as a decision tree: each node is one yes/no judgement, so the score is reproducible."""

import pytest

pytest.importorskip("deepeval")

from deepeval.metrics import DAGMetric
from deepeval.metrics.dag import (
    BinaryJudgementNode,
    DeepAcyclicGraph,
    VerdictNode,
)
from deepeval.test_case import LLMTestCase, SingleTurnParams

from evals.eval_judge import ClaudeJudge
from evals.eval_thresholds import JUDGED

PARAMS = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]

PARROTS = (
    "The input is the conversation so far, guests first. The output is the assistant's latest "
    "message. Does the output copy or summarise the guests' last message back to them "
    "(for example 'you are 2 adults and a child')? A short reaction in its own words does not count."
)
UNSURE = (
    "Does the guests' last message say they do not know or are not sure "
    "(for example 'no sé', 'not sure')? A greeting is not uncertainty."
)
OPTIONS = (
    "Does the output offer two or three concrete options for the guests to pick from, "
    "instead of repeating an open question?"
)
SOUNDS_HUMAN = (
    "Does the output react in a few words of its own and then ask one direct, short question, "
    "the way a person would? A bare form-like question with no reaction at all does not count."
)


def naturalness_metric(judge: ClaudeJudge) -> DAGMetric:
    sounds_human = BinaryJudgementNode(
        criteria=SOUNDS_HUMAN,
        evaluation_params=PARAMS,
        children=[
            VerdictNode(verdict=True, score=10),
            VerdictNode(verdict=False, score=4),
        ],
    )
    offers_options = BinaryJudgementNode(
        criteria=OPTIONS,
        evaluation_params=PARAMS,
        children=[
            VerdictNode(verdict=True, score=10),
            VerdictNode(verdict=False, score=2),
        ],
    )
    unsure = BinaryJudgementNode(
        criteria=UNSURE,
        evaluation_params=PARAMS,
        children=[
            VerdictNode(verdict=True, child=offers_options),
            VerdictNode(verdict=False, child=sounds_human),
        ],
    )
    parrots = BinaryJudgementNode(
        criteria=PARROTS,
        evaluation_params=PARAMS,
        children=[
            VerdictNode(verdict=True, score=0),
            VerdictNode(verdict=False, child=unsure),
        ],
    )
    return DAGMetric(
        name="Natural, non-robotic question",
        dag=DeepAcyclicGraph(root_nodes=[parrots]),
        model=judge,
        threshold=JUDGED,
        async_mode=False,
        include_reason=True,
    )


def naturalness_case(conversation: str, latest_message: str) -> LLMTestCase:
    return LLMTestCase(input=conversation, actual_output=latest_message)
