"""What the guests said, and what is private about them, never reaches an exported span."""

import copy
import json
from typing import Any

import pytest
from elicit_support import FakeExtractor
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from observability.obs_support import (
    ANTHROPIC_KEY,
    DATABASE_URL,
    ENV,
    INPUT,
    OUTPUT,
    Tracing,
    attribute_text,
    claude_planner,
    plan_scenario,
    planner,
    scenario,
    tracing_to,
)
from parkmind.services.clients.observability.langfuse_tracing import MASKED

THREAD_ID = "thread-masking"

PRIVATE = [
    pytest.param(
        "wiki_example_complete",
        ("My father can't walk long distances", "father", "LIMITED_WALKING"),
        id="limited_walking",
    ),
    pytest.param(
        "wheelchair_and_avoid",
        ("He uses a wheelchair", "wheelchair", "WHEELCHAIR", "husband"),
        id="wheelchair",
    ),
    pytest.param(
        "stated_child_height",
        ("he is about 4 feet tall", "4 feet", "hates waiting"),
        id="stated_height",
    ),
    pytest.param(
        "heat_sensitivity_is_accessibility",
        ("gets really sick in the heat", "heat_sensitivity"),
        id="heat_sensitivity",
    ),
]


@pytest.mark.parametrize(("scenario_id", "private"), PRIVATE)
def test_the_guests_words_and_accessibility_are_not_exported(
    tracing: Tracing,
    monkeypatch: pytest.MonkeyPatch,
    scenario_id: str,
    private: tuple[str, ...],
) -> None:
    graph = claude_planner(monkeypatch, scenario_id)

    plan_scenario(graph, tracing.config(THREAD_ID), scenario_id)

    text = attribute_text(tracing.spans())
    for message in scenario(scenario_id)["messages"]:
        assert message not in text
    for secret in private:
        assert secret not in text, f"{secret!r} reached an exported span"


def test_the_trace_is_not_empty_because_everything_was_hidden(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_scenario(claude_planner(monkeypatch), tracing.config(THREAD_ID))
    spans = tracing.spans()
    text = attribute_text(spans)

    assert MASKED in text
    assert "TRON" in text or "id-tron" in text
    assert '"party_size": 4' in text


def test_the_chat_inputs_and_outputs_keep_only_their_roles(
    tracing: Tracing, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_scenario(claude_planner(monkeypatch), tracing.config(THREAD_ID))

    for span in tracing.spans():
        if span.attributes.get("langfuse.observation.type") != "generation":
            continue
        for message in json.loads(span.attributes[INPUT]):
            assert set(message) <= {"role", "type", "name", "id"}
        assert json.loads(span.attributes[OUTPUT]) == {"role": "assistant"}


def test_a_guests_label_and_a_correction_are_masked(
    tracing: Tracing,
) -> None:
    label, correction = "Zebrina", "zebracorrection"
    extraction: dict[str, Any] = copy.deepcopy(
        dict(scenario("wiki_example_complete")["extraction"])
    )
    extraction["guests"][0]["label"] = label
    config = tracing.config(THREAD_ID)
    graph = planner(FakeExtractor(extraction))

    graph.invoke(
        {
            "thread_id": THREAD_ID,
            "messages": [
                HumanMessage(content=f"I'm {label}. {m}")
                for m in scenario("wiki_example_complete")["messages"]
            ],
        },
        config=config,
    )
    graph.invoke(
        Command(resume={"confirmed": False, "correction": correction}), config=config
    )

    text = attribute_text(tracing.spans())
    assert label not in text
    assert correction not in text


def test_secrets_in_the_environment_never_reach_a_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    with tracing_to(monkeypatch) as tracing:
        plan_scenario(claude_planner(monkeypatch), tracing.config(THREAD_ID))
        text = attribute_text(tracing.spans())

    assert text
    for secret in (
        ANTHROPIC_KEY,
        "sk-ant-",
        DATABASE_URL,
        "hunter2-canary",
        ENV["LANGFUSE_SECRET_KEY"],
        tracing.env["LANGFUSE_PUBLIC_KEY"],
    ):
        assert secret not in text, f"{secret!r} reached an exported span"
