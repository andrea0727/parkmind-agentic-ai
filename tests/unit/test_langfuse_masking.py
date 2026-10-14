"""The mask every Langfuse trace goes through (ADR 0003, C19): what is hidden, what stays."""

import json

import factories
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import Command

from parkmind.services.clients.observability.langfuse_tracing import (
    MASKED,
    mask_for_trace,
)

CANARY = "zebra-canary"


def _dumped(value: object) -> str:
    return json.dumps(mask_for_trace(data=value), default=str)


@pytest.mark.parametrize(
    "message",
    [
        HumanMessage(content=f"my dad can't walk much {CANARY}"),
        AIMessage(content=f"Got it, {CANARY}"),
        SystemMessage(content=CANARY),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "ElicitationExtraction", "args": {"note": CANARY}, "id": "c1"}
            ],
        ),
    ],
    ids=["guest", "ai", "system", "tool_call"],
)
def test_a_message_keeps_its_role_and_loses_its_text_and_tool_calls(message):
    masked = mask_for_trace(data=message)

    assert masked["type"] == message.type
    assert CANARY not in json.dumps(masked)


def test_a_message_in_the_handlers_dict_form_keeps_only_its_role():
    """The handler records chat inputs as ``{"role", "content", ...}`` dicts."""
    message = {
        "role": "assistant",
        "content": CANARY,
        "tool_calls": [{"function": {"arguments": json.dumps({"x": CANARY})}}],
    }

    assert mask_for_trace(data=message) == {"role": "assistant"}


@pytest.mark.parametrize(
    "key",
    [
        "correction",
        "label",
        "echo",
        "explanation",
        "accessibility",
        "mobility_requirements",
        "ride_restrictions",
        "daily_walking_limit_minutes",
        "accessibility_requirements",
    ],
)
def test_sensitive_keys_are_masked_wherever_they_appear(key):
    data = {"outer": [{"inner": {key: CANARY}}]}

    assert mask_for_trace(data=data) == {"outer": [{"inner": {key: MASKED}}]}


def test_a_resume_with_a_correction_is_masked_inside_the_command():
    resume = Command(resume={"interrupt-1": {"confirmed": False, "correction": CANARY}})

    assert CANARY not in _dumped(resume)


def test_the_elicit_prompts_guest_block_is_masked_as_a_plain_string():
    prompt = f"Guest messages, oldest first:\n1. {CANARY}"

    assert mask_for_trace(data={"prompt": prompt}) == {"prompt": MASKED}


def test_a_guests_label_is_masked_inside_typed_constraints():
    constraints = factories.party_constraints(
        guests=[factories.guest(guest_id="g1", label=CANARY)]
    )

    masked = mask_for_trace(data={"constraints": constraints})

    assert CANARY not in json.dumps(masked)
    assert masked["constraints"]["guests"][0]["guest_id"] == "g1"


def test_typed_outputs_are_traced_as_they_are():
    plan, event = factories.plan(), factories.event()

    masked = mask_for_trace(data={"plan": plan, "thread_id": "t1", "valid": True})

    assert masked["plan"] == plan.model_dump(mode="json")
    assert (masked["thread_id"], masked["valid"]) == ("t1", True)
    assert mask_for_trace(data=event) == event.model_dump(mode="json")


def test_a_proposal_is_traced_without_its_explanation():
    """A template explanation names the guests by their label."""
    proposal = factories.proposal(explanation=f"{CANARY}, your plan is ready")

    masked = mask_for_trace(data=proposal)

    assert masked["explanation"] == MASKED
    assert masked["proposal_id"] == proposal.proposal_id


def test_accessibility_requirements_never_reach_a_trace_even_by_mistake():
    requirements = factories.accessibility()

    masked = json.dumps(mask_for_trace(data={"oops": requirements}))

    assert "WHEELCHAIR" not in masked
    assert "NOT_RECOMMENDED_HIGH_G_FORCE" not in masked
    assert '"daily_walking_limit_minutes": 90' not in masked


def test_an_unknown_object_is_traced_by_its_type_name_only():
    class Opaque:
        def __repr__(self) -> str:
            return CANARY

    assert mask_for_trace(data={"x": Opaque()}) == {"x": "<Opaque>"}


def test_a_cycle_is_masked_instead_of_recursing_forever():
    loop: list[object] = []
    loop.append(loop)

    assert MASKED in json.dumps(mask_for_trace(data=loop))
