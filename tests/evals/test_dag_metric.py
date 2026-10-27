"""The naturalness decision tree scores by the path the judge's yes/no answers take, not by a free-form number."""

from typing import Any

import pytest
from pydantic import BaseModel

pytest.importorskip("deepeval")

from deepeval.metrics import DAGMetric

from evals.eval_dag import naturalness_case, naturalness_metric
from evals.eval_judge import ClaudeJudge
from evals.eval_thresholds import JUDGED


class ScriptedJudge(ClaudeJudge):
    """Answers each yes/no node from a script, so the tree's arithmetic is tested without a model."""

    def __init__(self, *verdicts: bool) -> None:
        super().__init__(model="scripted", api_key="")
        self._verdicts = list(verdicts)
        self.asked = 0

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        assert schema is not None
        fields = schema.model_fields
        if "verdict" in fields:
            self.asked += 1
            return schema(verdict=self._verdicts.pop(0), reason="scripted")
        return schema(reason="scripted")

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> Any:
        return self.generate(prompt, schema)


def _score(*verdicts: bool) -> tuple[float, int]:
    judge = ScriptedJudge(*verdicts)
    metric = naturalness_metric(judge)
    metric.measure(naturalness_case("hola\nno sé", "Tranquilo. ¿Solos o en familia?"))
    return metric.score or 0.0, judge.asked


def test_the_metric_is_a_decision_tree_judged_by_the_shared_threshold() -> None:
    metric = naturalness_metric(ScriptedJudge())
    assert isinstance(metric, DAGMetric)
    assert metric.threshold == JUDGED


def test_a_message_that_parrots_the_guests_scores_zero_after_one_question() -> None:
    assert _score(True) == (0.0, 1)


def test_unsure_guests_who_get_options_score_full_marks() -> None:
    assert _score(False, True, True) == (1.0, 3)


def test_unsure_guests_who_get_no_options_fail() -> None:
    score, asked = _score(False, True, False)
    assert score < JUDGED and asked == 3


def test_confident_guests_get_a_human_reaction_and_one_question() -> None:
    assert _score(False, False, True) == (1.0, 3)


def test_a_form_like_question_without_any_reaction_fails() -> None:
    score, _ = _score(False, False, False)
    assert score < JUDGED
