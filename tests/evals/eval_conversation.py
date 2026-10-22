"""Conversation flow: across turns the assistant never repeats a question and never parrots the guests."""

import re
from collections.abc import Sequence
from typing import Any

import pytest

pytest.importorskip("deepeval")

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

from evals.eval_thresholds import CONVERSATION_FLOW

Transcript = Sequence[tuple[str, str]]
ECHO_WORDS = 4


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _runs(text: str) -> set[tuple[str, ...]]:
    words = _words(text)
    return {
        tuple(words[i : i + ECHO_WORDS]) for i in range(len(words) - ECHO_WORDS + 1)
    }


def flow_problems(turns: Transcript) -> list[str]:
    """One finding per assistant turn that repeats an earlier one or copies the guests' last words."""
    problems: list[str] = []
    said: list[str] = []
    last_guest = ""
    for position, (role, text) in enumerate(turns, start=1):
        if role == "guest":
            last_guest = text
            continue
        if _words(text) in [_words(earlier) for earlier in said]:
            problems.append(f"turn {position} repeats an earlier assistant turn")
        if _runs(last_guest) & _runs(text):
            problems.append(f"turn {position} repeats back what the guests wrote")
        said.append(text)
    return problems


def conversation_test_case(turns: Transcript) -> LLMTestCase:
    guests = [text for role, text in turns if role == "guest"]
    replies = [text for role, text in turns if role != "guest"]
    return LLMTestCase(
        input="\n".join(guests),
        actual_output="\n".join(replies),
        additional_metadata={"turns": [list(turn) for turn in turns]},
    )


class ConversationFlowMetric(BaseMetric):
    """Deterministic: no assistant turn repeats an earlier one or echoes the guests' words."""

    def __init__(self, threshold: float = CONVERSATION_FLOW) -> None:
        self.threshold = threshold
        self.async_mode = False
        self.strict_mode = False
        self.include_reason = True
        self.evaluation_model = None
        self.error: str | None = None

    @property
    def __name__(self) -> str:
        return "Conversation flow"

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        turns = [
            (str(role), str(text))
            for role, text in (test_case.additional_metadata or {}).get("turns", [])
        ]
        problems = flow_problems(turns)
        self.score = 0.0 if problems else 1.0
        self.reason = "; ".join(problems) or "no repeated question and no echo"
        self.success = self.is_successful()
        return self.score

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)
