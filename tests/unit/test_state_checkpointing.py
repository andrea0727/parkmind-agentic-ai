"""LangGraph checkpoint/resume tests for the ParkMindState schema.

Covers:
- state can be checkpointed and resumed via a real LangGraph checkpointer
- state serialization is stable (round-trips without loss)
- AccessibilityRequirements is never present in checkpoint payloads [C19]
"""

import typing
from datetime import datetime
from pathlib import Path

import factories
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from parkmind.core.contracts import (
    PARK_TZ,
    AccessibilityRequirements,
    CheckResult,
    EventThresholds,
    FairnessConfig,
    GroupObjective,
    HardConstraintSet,
    PartyConstraints,
    PlanDiff,
    RejectionReason,
)
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.state import ParkMindState

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "parkmind"

# Field names that only exist on AccessibilityRequirements (the hard-constraint
# payload) and never on AccessibilityCheck (the derived, checkpoint-safe
# eligibility result) or any other §33 contract. "ride_restrictions" is
# deliberately excluded: HardConstraintSet.ride_restrictions is a distinct,
# aggregate, non-PII field (guest_id -> list[RideRestriction]) that's fine
# to checkpoint via GroupObjective.
_ACCESSIBILITY_REQUIREMENTS_MARKERS = (
    "mobility_requirements",
    "daily_walking_limit_minutes",
    "rest_frequency_minutes",
    "retention_policy",
)


def _build_full_state() -> ParkMindState:
    """A realistically populated state touching every field in the schema."""
    constraints = PartyConstraints(
        party_size=1,
        guests=[factories.guest()],
        departure_time=datetime(2026, 9, 16, 20, 0, tzinfo=PARK_TZ),
        constraints_version=1,
    )
    plan = factories.plan()
    return {
        "thread_id": "thread_1",
        "messages": [],
        "iteration": 3,
        "constraints": constraints,
        "constraints_valid": True,
        "guest_profiles": [factories.guest_profile()],
        "accessibility_ref": ["g1"],  # guest ids only [C19]
        "group_objective": GroupObjective(
            objective_version="v1",
            weights={},
            hard_constraints=HardConstraintSet(),
            fairness=FairnessConfig(lambda_fairness=0.5, min_satisfaction_floor=0.5),
            event_thresholds=EventThresholds(),
        ),
        "live_context": factories.live_context(),
        "execution_state": factories.execution_state(),
        "current_plan": plan,
        "candidate_plan": None,
        "events": [factories.event()],
        "event_confirmation": "PENDING",
        "check_result": CheckResult(valid=True),
        "diff": PlanDiff(),
        "proposal": factories.proposal(),
        "approval": "PENDING",
        "rejection_reason": RejectionReason.TOO_MUCH_WALKING,
        "provenance": [factories.provenance()],
        "preference_model_version": "v1",
        "pending_hard_constraint_confirmation": None,
    }


def _compile_graph_with_checkpointer(saver: MemorySaver):
    def passthrough(state: ParkMindState) -> ParkMindState:
        return {}

    graph = StateGraph(ParkMindState)
    graph.add_node("noop", passthrough)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)
    return graph.compile(checkpointer=saver)


def _all_serialized_bytes(saver: MemorySaver) -> bytes:
    """Concatenate every raw (type, bytes) payload the saver ever wrote."""
    chunks: list[bytes] = []
    for checkpoints in saver.storage.values():
        for versions in checkpoints.values():
            for checkpoint_tuple, _metadata, _parent in versions.values():
                chunks.append(checkpoint_tuple[1])
    for writes in saver.writes.values():
        for _task_id, _channel, payload, _task_path in writes.values():
            chunks.append(payload[1])
    return b"".join(chunks)


def test_state_schema_never_types_accessibility_requirements():
    """Structural guard: no ParkMindState field may be typed as AccessibilityRequirements."""
    hints = typing.get_type_hints(ParkMindState, include_extras=True)
    for field_name, hint in hints.items():
        assert "AccessibilityRequirements" not in str(hint), (
            f"ParkMindState.{field_name} must not reference AccessibilityRequirements; "
            "only accessibility_ref (guest ids) may be checkpointed [C19]"
        )


def test_no_graph_or_agent_code_constructs_accessibility_requirements():
    """No node in graph/ or agents/ may build the hard-constraint payload.

    AccessibilityRequirements is loaded per-run from the session store and
    must never flow into ParkMindState (and therefore never into a
    checkpoint). This greps the actual orchestration source, not just the
    schema, so a node that smuggles it in via a loosely-typed dict write
    still fails the check.
    """
    offending: list[str] = []
    for directory in ("graph", "agents"):
        for path in (SRC_ROOT / directory).rglob("*.py"):
            text = path.read_text()
            if "AccessibilityRequirements(" in text:
                offending.append(str(path))
    assert not offending, f"AccessibilityRequirements constructed in: {offending}"


def test_only_interrupt_approval_calls_approve_plan():
    """Only the human-approval node may activate a plan.

    approve_plan() promotes candidate_plan to current_plan; it must never be
    called from an agent/LLM node or any other graph node, only from the
    resumed, human-driven path in _interrupt_for_approval.
    """
    node_module = SRC_ROOT / "graph" / "initial_planning_graph.py"
    callers: list[str] = []
    for directory in ("graph", "agents"):
        for path in (SRC_ROOT / directory).rglob("*.py"):
            if path.name == "state_helpers.py":
                continue  # the definition, not a call site
            if "approve_plan(" in path.read_text():
                callers.append(str(path))

    assert callers == [str(node_module)], (
        f"approve_plan() must only be called from {node_module}, found: {callers}"
    )

    source = node_module.read_text()
    start = source.index("def _interrupt_for_approval")
    end = source.find("\ndef ", start + 1)
    body = source[start : end if end != -1 else None]
    assert "approve_plan(" in body, (
        "approve_plan() call must live inside _interrupt_for_approval"
    )


def test_state_checkpoints_and_resumes():
    """A state written by one invocation is readable via get_state (resume)."""
    saver = default_checkpointer()
    compiled = _compile_graph_with_checkpointer(saver)
    config = {"configurable": {"thread_id": "thread_1"}}

    compiled.invoke(_build_full_state(), config=config)

    snapshot = compiled.get_state(config)
    assert snapshot.values["thread_id"] == "thread_1"
    assert snapshot.values["current_plan"] == factories.plan()
    assert snapshot.values["accessibility_ref"] == ["g1"]


def test_state_serialization_is_stable():
    """Round-tripping through the checkpointer must not lose or mutate data."""
    saver = default_checkpointer()
    compiled = _compile_graph_with_checkpointer(saver)
    config = {"configurable": {"thread_id": "thread_1"}}

    original = _build_full_state()
    compiled.invoke(original, config=config)

    restored = compiled.get_state(config).values
    for key, value in original.items():
        assert restored[key] == value, f"field {key!r} did not round-trip"


def test_accessibility_requirements_never_appear_in_checkpoint_payload():
    """Even at the raw serialized-bytes level, no AccessibilityRequirements
    field name (consent, mobility_requirements, ride_restrictions, ...) is
    ever written to a checkpoint, because the schema has no field for it.
    """
    saver = default_checkpointer()
    compiled = _compile_graph_with_checkpointer(saver)
    config = {"configurable": {"thread_id": "thread_1"}}
    compiled.invoke(_build_full_state(), config=config)

    raw = _all_serialized_bytes(saver)
    for marker in _ACCESSIBILITY_REQUIREMENTS_MARKERS:
        assert marker.encode() not in raw, f"found {marker!r} in checkpoint payload"

    # Sanity check the assertion isn't vacuous: AccessibilityRequirements
    # really does carry these markers when it's actually constructed.
    sample = AccessibilityRequirements(guest_id="g1", consent=False)
    assert "retention_policy" in sample.model_dump()
