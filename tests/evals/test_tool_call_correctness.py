"""The extraction model must call the extraction tool exactly once, with valid arguments, and nothing else."""

import os
from typing import Any

import pytest

pytest.importorskip("deepeval")

from deepeval.test_case import ToolCall
from elicit_support import scenario

from evals.eval_tool_calls import (
    EXTRACTION_TOOL,
    RecordingModel,
    ScriptedToolModel,
    ToolArgumentsSchemaMetric,
    extraction_tool_test_case,
    extractor_over,
    tool_call,
    tool_correctness_metric,
)
from parkmind.agents.elicit_agent import (
    AnthropicExtractor,
    ExtractorOutputError,
    extract_with_recovery,
)

REQUEST = scenario("wiki_example_complete")["messages"]
GOOD_ARGS: dict[str, Any] = scenario("wiki_example_complete")["extraction"]


def run(
    *replies: list[dict[str, Any]], max_attempts: int = 1
) -> tuple[RecordingModel, ScriptedToolModel]:
    scripted = ScriptedToolModel(*replies)
    recorder = RecordingModel(scripted)
    try:
        extract_with_recovery(
            extractor_over(recorder), REQUEST, max_attempts=max_attempts
        )
    except ExtractorOutputError:
        pass
    return recorder, scripted


def measured(recorder: RecordingModel) -> tuple[bool, bool]:
    case = extraction_tool_test_case(" ".join(REQUEST), recorder.tools_called)
    right_tool = tool_correctness_metric()
    right_tool.measure(case)
    schema = ToolArgumentsSchemaMetric()
    schema.measure(case)
    return bool(right_tool.is_successful()), bool(schema.is_successful())


def test_one_extraction_call_with_valid_arguments_is_correct() -> None:
    recorder, _ = run([tool_call(EXTRACTION_TOOL, GOOD_ARGS)])
    assert measured(recorder) == (True, True)


def test_the_extractor_reads_the_arguments_of_the_first_call() -> None:
    recorder, _ = run([tool_call(EXTRACTION_TOOL, GOOD_ARGS)])
    arguments = recorder.tools_called[0].input_parameters or {}
    assert arguments["departure_time"] == "20:00"


def test_a_reply_without_any_tool_call_fails_tool_correctness() -> None:
    recorder, _ = run([])
    assert recorder.tools_called == []
    assert measured(recorder)[0] is False


def test_a_missing_call_also_fails_the_schema_check() -> None:
    recorder, _ = run([])
    assert measured(recorder)[1] is False


def test_a_call_to_another_tool_fails_tool_correctness() -> None:
    recorder, _ = run([tool_call("book_tickets", GOOD_ARGS)])
    assert measured(recorder)[0] is False


def test_an_extra_tool_next_to_the_extraction_fails_tool_correctness() -> None:
    recorder, _ = run(
        [tool_call(EXTRACTION_TOOL, GOOD_ARGS), tool_call("book_tickets", {})]
    )
    assert measured(recorder)[0] is False


def test_two_extraction_calls_in_one_reply_fail_tool_correctness() -> None:
    recorder, _ = run(
        [tool_call(EXTRACTION_TOOL, GOOD_ARGS), tool_call(EXTRACTION_TOOL, GOOD_ARGS)]
    )
    assert measured(recorder)[0] is False


def test_arguments_that_break_the_schema_fail_the_schema_check() -> None:
    recorder, _ = run(
        [tool_call(EXTRACTION_TOOL, {**GOOD_ARGS, "departure_time": "8 PM"})]
    )
    right_tool, schema = measured(recorder)
    assert right_tool is True
    assert schema is False


def test_an_argument_outside_the_schema_fails_the_schema_check() -> None:
    recorder, _ = run(
        [tool_call(EXTRACTION_TOOL, {**GOOD_ARGS, "diagnosis": "asthma"})]
    )
    assert measured(recorder)[1] is False


def test_a_party_size_that_disagrees_with_the_guests_fails_the_schema_check() -> None:
    recorder, _ = run([tool_call(EXTRACTION_TOOL, {**GOOD_ARGS, "party_size": 9})])
    assert measured(recorder)[1] is False


def test_the_schema_failure_names_the_offending_field() -> None:
    recorder, _ = run(
        [tool_call(EXTRACTION_TOOL, {**GOOD_ARGS, "departure_time": "8 PM"})]
    )
    schema = ToolArgumentsSchemaMetric()
    schema.measure(extraction_tool_test_case("", recorder.tools_called))
    assert "departure_time" in str(schema.reason)


def test_a_repaired_extraction_costs_a_second_call_and_fails_exactly_once() -> None:
    bad = tool_call(EXTRACTION_TOOL, {**GOOD_ARGS, "departure_time": "8 PM"})
    good = tool_call(EXTRACTION_TOOL, GOOD_ARGS)
    recorder, scripted = run([bad], [good], max_attempts=2)
    assert scripted.invocations == 2
    assert len(recorder.tools_called) == 2
    assert measured(recorder)[0] is False


def test_tool_correctness_makes_no_judge_call() -> None:
    metric = tool_correctness_metric()
    case = extraction_tool_test_case(
        "x", [ToolCall(name=EXTRACTION_TOOL, input_parameters={})]
    )
    metric.measure(case)
    assert metric.score == 1.0


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
def test_the_real_model_calls_the_extraction_tool_once_with_valid_arguments() -> None:
    extractor = AnthropicExtractor()
    recorder = RecordingModel(extractor._runnable())
    extractor._llm = recorder
    extractor.extract(REQUEST)
    assert measured(recorder) == (True, True)
