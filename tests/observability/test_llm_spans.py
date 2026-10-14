"""The model calls: what a generation span records about the call, and what it hides."""

import json

import pytest
from opentelemetry.sdk.trace import ReadableSpan

from observability.obs_support import (
    ANTHROPIC_KEY,
    INPUT,
    LLM_DELAY_SECONDS,
    MODEL,
    MODEL_NAME,
    OUTPUT,
    USAGE,
    USAGE_TOKENS,
    Tracing,
    attribute_text,
    claude_planner,
    duration_seconds,
    generations,
    plan_scenario,
)

THREAD_ID = "thread-llm"
META = "langfuse.observation.metadata."


def _generations(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch, **kwargs: object
) -> list[ReadableSpan]:
    plan_scenario(claude_planner(monkeypatch, **kwargs), tracing.config(THREAD_ID))
    return generations(tracing.spans())


def test_each_model_call_is_a_generation_under_the_node_that_made_it(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch
) -> None:
    spans = _generations(tracing, monkeypatch)

    assert [s.attributes[META + "langgraph_node"] for s in spans] == [
        "elicit",
        "explain",
    ]


def test_a_generation_names_the_model_provider_and_parameters(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch
) -> None:
    for span in _generations(tracing, monkeypatch):
        assert span.attributes[MODEL] == MODEL_NAME
        assert span.attributes[META + "ls_provider"] == "anthropic"
        assert span.attributes[META + "ls_model_name"] == MODEL_NAME
        assert json.loads(span.attributes["langfuse.observation.model.parameters"]) == {
            "temperature": 0.0,
            "max_tokens": 1500,
        }


def test_the_forced_tool_call_is_traced_by_role_only(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch
) -> None:
    [elicit, _] = _generations(tracing, monkeypatch)

    roles = [m["role"] for m in json.loads(elicit.attributes[INPUT])]
    assert {"system", "user"} <= set(roles)
    assert json.loads(elicit.attributes[OUTPUT]) == {"role": "assistant"}
    assert "ElicitationExtraction" not in attribute_text([elicit])
    assert "party_size" not in attribute_text([elicit])


def test_token_usage_reported_by_the_model_is_on_its_generation(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch
) -> None:
    for span in _generations(tracing, monkeypatch):
        assert json.loads(span.attributes[USAGE]) == {
            "input": USAGE_TOKENS["input_tokens"],
            "output": USAGE_TOKENS["output_tokens"],
            "total": USAGE_TOKENS["total_tokens"],
        }


def test_a_model_that_reports_no_usage_leaves_the_field_out_without_failing(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch
) -> None:
    spans = _generations(tracing, monkeypatch, usage=None)

    assert len(spans) == 2
    assert all(USAGE not in s.attributes for s in spans)


def test_a_generations_latency_covers_the_model_call_and_fits_in_its_node(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_scenario(claude_planner(monkeypatch), tracing.config(THREAD_ID))
    spans = tracing.spans()
    by_id = {s.context.span_id: s for s in spans}

    for generation in generations(spans):
        latency = duration_seconds(generation)
        assert latency >= LLM_DELAY_SECONDS
        ancestors = []
        parent = generation.parent
        while parent is not None and parent.span_id in by_id:
            ancestors.append(by_id[parent.span_id])
            parent = ancestors[-1].parent
        node = next(
            a
            for a in ancestors
            if a.name == generation.attributes[META + "langgraph_node"]
        )
        assert latency <= duration_seconds(node)


def test_the_api_key_given_to_the_client_is_not_in_the_generation(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch
) -> None:
    spans = _generations(tracing, monkeypatch)

    assert ANTHROPIC_KEY not in attribute_text(spans)
