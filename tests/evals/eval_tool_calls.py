"""Tool-call evals: what the extraction model called, scored against what it should have called."""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

pytest.importorskip("deepeval")

from deepeval.metrics import BaseMetric, ToolCorrectnessMetric
from deepeval.test_case import LLMTestCase, ToolCall
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from evals.eval_judge import ClaudeJudge
from evals.eval_thresholds import TOOL_CORRECTNESS
from parkmind.agents.elicit_agent import (
    AnthropicExtractor,
    without_empty_accessibility,
)
from parkmind.agents.elicit_schema import ElicitationExtraction

EXTRACTION_TOOL = ElicitationExtraction.__name__


def tool_call(name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": dict(args), "id": f"call_{name}"}


class ScriptedToolModel:
    """Stands in for the bound chat model: answers each invoke with the next scripted tool calls."""

    def __init__(self, *replies: Sequence[Mapping[str, Any]]) -> None:
        self._replies = list(replies)
        self.invocations = 0

    def invoke(self, messages: Sequence[Any]) -> AIMessage:
        reply = self._replies[min(self.invocations, len(self._replies) - 1)]
        self.invocations += 1
        return AIMessage(content="", tool_calls=[dict(call) for call in reply])


class RecordingModel:
    """Wraps a model and keeps every tool call it returned, as DeepEval ToolCalls."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.tools_called: list[ToolCall] = []

    def invoke(self, messages: Sequence[Any]) -> Any:
        response = self._inner.invoke(messages)
        for call in getattr(response, "tool_calls", None) or []:
            self.tools_called.append(
                ToolCall(name=call["name"], input_parameters=dict(call.get("args", {})))
            )
        return response


def extractor_over(model: Any) -> AnthropicExtractor:
    extractor = AnthropicExtractor(api_key="not-used")
    extractor._llm = model
    return extractor


def extraction_tool_test_case(
    request: str, tools_called: Sequence[ToolCall]
) -> LLMTestCase:
    return LLMTestCase(
        input=request,
        actual_output="",
        tools_called=list(tools_called),
        expected_tools=[ToolCall(name=EXTRACTION_TOOL, input_parameters={})],
    )


def tool_correctness_metric(
    judge: ClaudeJudge | None = None,
) -> ToolCorrectnessMetric:
    """Exactly the extraction tool, once; names only, since arguments vary with every request."""
    return ToolCorrectnessMetric(
        threshold=TOOL_CORRECTNESS,
        should_exact_match=True,
        model=judge or ClaudeJudge(),
        async_mode=False,
        include_reason=False,
    )


class ToolArgumentsSchemaMetric(BaseMetric):
    """Every extraction call's arguments validate against the ElicitationExtraction schema."""

    def __init__(self, threshold: float = TOOL_CORRECTNESS) -> None:
        self.threshold = threshold
        self.async_mode = False
        self.strict_mode = False
        self.include_reason = True
        self.evaluation_model = None
        self.error: str | None = None

    @property
    def __name__(self) -> str:
        return "Tool Arguments Schema"

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        calls = [
            call
            for call in test_case.tools_called or []
            if call.name == EXTRACTION_TOOL
        ]
        problems = [problem for call in calls for problem in _schema_problems(call)]
        self.score = 0.0 if problems or not calls else 1.0
        self.reason = "; ".join(problems) or (
            "arguments satisfy the schema"
            if calls
            else "no extraction call to validate"
        )
        self.success = self.is_successful()
        return self.score

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)


def _schema_problems(call: ToolCall) -> list[str]:
    try:
        ElicitationExtraction.model_validate(
            without_empty_accessibility(call.input_parameters or {})
        )
    except ValidationError as exc:
        return [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]
    return []
