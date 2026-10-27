"""ClaudeJudge as a DeepEval model: lazy, key-free to build, and schema-aware."""

import asyncio

import pytest
from pydantic import BaseModel

pytest.importorskip("deepeval")

from deepeval.models import DeepEvalBaseLLM
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)
from langchain_core.messages import AIMessage

from evals.eval_judge import ClaudeJudge, JudgeUnavailableError


class Verdict(BaseModel):
    score: float
    reason: str


class _PromptRecorder:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> AIMessage:
        self.prompts.append(prompt)
        return AIMessage(content=self._reply)


def _judge(*replies: str) -> ClaudeJudge:
    return ClaudeJudge(chat=FakeListChatModel(responses=list(replies)))


def test_the_judge_is_built_without_a_key_or_a_client() -> None:
    judge = ClaudeJudge(api_key="")

    assert isinstance(judge, DeepEvalBaseLLM)
    assert judge.model is None


def test_the_judge_reports_the_model_it_was_given() -> None:
    assert (
        ClaudeJudge(model="claude-test", api_key="").get_model_name() == "claude-test"
    )


def test_calling_the_judge_without_a_key_says_so_instead_of_reaching_the_network() -> (
    None
):
    with pytest.raises(JudgeUnavailableError):
        ClaudeJudge(api_key="").generate("rate this")


def test_the_judge_returns_the_model_text_when_no_schema_is_asked_for() -> None:
    assert _judge("a plain answer").generate("rate this") == "a plain answer"


def test_the_judge_parses_json_into_the_requested_schema() -> None:
    verdict = _judge('{"score": 0.8, "reason": "grounded"}').generate(
        "rate this", schema=Verdict
    )

    assert verdict == Verdict(score=0.8, reason="grounded")


def test_the_judge_reads_json_wrapped_in_a_code_fence_and_prose() -> None:
    reply = 'Here you go:\n```json\n{"score": 1, "reason": "ok"}\n```'

    assert _judge(reply).generate("rate this", schema=Verdict).score == 1


def test_the_judge_rejects_a_reply_that_does_not_fit_the_schema() -> None:
    with pytest.raises(ValueError):
        _judge('{"score": "high"}').generate("rate this", schema=Verdict)


def test_the_async_judge_answers_like_the_sync_one() -> None:
    judge = _judge('{"score": 0.5, "reason": "mixed"}')

    verdict = asyncio.run(judge.a_generate("rate this", schema=Verdict))

    assert verdict == Verdict(score=0.5, reason="mixed")


def test_deepeval_routes_schema_requests_through_the_judge() -> None:
    judge = _judge('{"score": 0.2, "reason": "weak"}')

    assert judge.generate_with_schema("rate this", schema=Verdict).score == 0.2


def test_the_schema_is_described_to_the_model_in_the_prompt() -> None:
    chat = _PromptRecorder('{"score": 1, "reason": "ok"}')

    ClaudeJudge(chat=chat).generate("rate this", schema=Verdict)

    assert "rate this" in chat.prompts[0]
    assert '"score"' in chat.prompts[0]
