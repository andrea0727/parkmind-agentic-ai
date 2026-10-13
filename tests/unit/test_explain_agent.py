"""EXPLAIN's safety path (section 40 [C17], P0-31): grounded LLM text or the template."""

import logging
from collections.abc import Mapping
from typing import Any

import factories
import pytest

from parkmind.agents.explain_agent import (
    EXPLAIN_PROMPT_VERSION,
    TEMPLATE_VERSION,
    NarratorUnavailableError,
    explain_grounded,
)
from parkmind.core.contracts import CheckResult
from parkmind.services.use_cases.explain_plan import (
    PreparedExplanation,
    explain_plan,
    explanation_payload,
)

NAMES = {"a1": "Space Mountain"}
PLAN = factories.plan()  # one stop a1: 20 min wait, 5 min walk
GROUNDED = "Space Mountain first: a 20 minute wait and a 5 minute walk."
INVENTED = "Space Mountain first: a 45 minute wait."


def _prepared() -> PreparedExplanation:
    check = CheckResult(valid=True)
    return PreparedExplanation(
        explanation_payload(PLAN, check, NAMES), explain_plan(PLAN, check, NAMES)
    )


class _Scripted:
    """Answers in order; an Exception in the script is raised instead."""

    def __init__(self, *answers: Any) -> None:
        self._answers = list(answers)
        self.hints: list[str | None] = []

    def narrate(
        self, payload_json: str, repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        self.hints.append(repair_hint)
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_grounded_first_answer_is_used():
    narrator = _Scripted({"text": GROUNDED})

    result = explain_grounded(narrator, _prepared())

    assert (result.text, result.source, result.attempts) == (GROUNDED, "llm", 1)
    assert result.prompt_version == EXPLAIN_PROMPT_VERSION
    assert narrator.hints == [None]


def test_an_ungrounded_answer_is_retried_once_with_the_numbers_listed():
    narrator = _Scripted({"text": INVENTED}, {"text": GROUNDED})

    result = explain_grounded(narrator, _prepared())

    assert (result.source, result.attempts) == ("llm", 2)
    assert narrator.hints[1] is not None and "45" in narrator.hints[1]


def test_two_ungrounded_answers_fall_back_to_the_template():
    narrator = _Scripted({"text": INVENTED}, {"text": INVENTED})

    result = explain_grounded(narrator, _prepared())

    assert result.source == "template" and result.attempts == 2
    assert result.text == _prepared().template
    assert result.prompt_version == TEMPLATE_VERSION


def test_a_schema_failure_is_retried_once_then_falls_back():
    narrator = _Scripted({"wrong": "field"}, {"text": ""})

    result = explain_grounded(narrator, _prepared())

    assert result.source == "template" and len(narrator.hints) == 2
    assert narrator.hints[1] is not None and "schema" in narrator.hints[1]


@pytest.mark.parametrize(
    "failure",
    [NarratorUnavailableError("no key"), TimeoutError("network"), RuntimeError("boom")],
    ids=["no_key", "timeout", "anything"],
)
def test_a_narrator_that_cannot_answer_never_breaks_the_plan(failure: Exception):
    result = explain_grounded(_Scripted(failure), _prepared())

    assert result.source == "template"


def test_without_a_narrator_the_template_is_used():
    assert explain_grounded(None, _prepared()).source == "template"


def test_logs_carry_neither_the_payload_nor_the_model_text(caplog):
    caplog.set_level(logging.DEBUG, logger="parkmind.agents.explain_agent")
    narrator = _Scripted({"text": INVENTED}, RuntimeError(INVENTED))

    explain_grounded(narrator, _prepared())

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert logged
    for secret in ("Space Mountain", "45", INVENTED):
        assert secret not in logged
