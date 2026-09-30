"""LangGraph checkpoint/resume tests for the ParkMindState schema.

Covers:
- state can be checkpointed and resumed via a real LangGraph checkpointer
- state serialization is stable (round-trips without loss)
- AccessibilityRequirements is never present in checkpoint payloads [C19]
"""

import ast
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
# deliberately excluded: HardConstraintSet.ride_restrictions is a derived,
# aggregated field (guest_id -> list[RideRestriction]) that reaches the
# checkpoint through GroupObjective. Whether that derived shape counts as
# PII is still open under the C19 discussion, so treat it as "derived,
# pending C19" rather than as decided non-PII.
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


_APPROVAL_PRIVILEGED_MODULE = "initial_planning_graph.py"
_APPROVAL_PRIVILEGED_FUNCTION = "_interrupt_for_approval"


def _iter_source_files():
    for directory in ("graph", "agents"):
        for path in (SRC_ROOT / directory).rglob("*.py"):
            if path.name == "state_helpers.py":
                continue  # holds the approval helpers themselves
            yield path


def _top_level_functions(module: ast.Module):
    """Yield ``(FunctionDef, name)`` for every function defined at module scope.

    Nested functions are covered when their outer function is walked; yielding
    only top-level defs (plus the module-scope "function" below) prevents
    double-reporting the same violation under both the inner and the outer
    function.
    """
    for node in module.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node, node.name


def _module_scope_statements(module: ast.Module) -> list[ast.stmt]:
    """Module-level statements that are NOT function or class definitions.

    A ``state["approval"] = "APPROVED"`` or a ``PlanRepository(...).activate(...)``
    at module scope would slip past a function-only walk, so treat everything
    else at the top level as one synthetic "module init" body.
    """
    return [
        stmt
        for stmt in module.body
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]


def _find_approval_violations(scope: ast.AST | list[ast.stmt]) -> list[str]:
    """Return descriptions of any plan-activation code inside ``scope``.

    Flags every way a node could activate a plan without going through the
    resumed, human-driven interrupt path:
    - calls to ``approve_plan(...)``
    - references to ``ResolveProposalUseCase`` (the use case that flips a
      proposal to APPROVED and activates the plan in the repository)
    - ``.activate(...)`` calls -- deliberately broad. Today the only
      ``.activate`` in ``graph/`` + ``agents/`` is inside
      ``_interrupt_for_approval`` (via ``ResolveProposalUseCase`` on the
      plan repository), so a broad ban catches every hand-rolled path
      into the ``plans`` table without a heuristic on the receiver
      name. A future unrelated ``.activate`` API in this tree is a signal
      to think, not noise.
    - assignments whose RHS is the string literal ``"APPROVED"`` (a hand-set
      ``state["approval"] = "APPROVED"`` bypasses ``approve_plan``)
    """
    nodes: list[ast.AST]
    if isinstance(scope, list):
        nodes = []
        for stmt in scope:
            nodes.extend(ast.walk(stmt))
    else:
        nodes = list(ast.walk(scope))

    findings: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Call):
            called = node.func
            if isinstance(called, ast.Name) and called.id == "approve_plan":
                findings.append(f"call to approve_plan() at line {node.lineno}")
            elif isinstance(called, ast.Attribute) and called.attr == "approve_plan":
                findings.append(f"call to .approve_plan() at line {node.lineno}")
            elif isinstance(called, ast.Attribute) and called.attr == "activate":
                findings.append(f"call to .activate() at line {node.lineno}")
        if isinstance(node, ast.Name) and node.id == "ResolveProposalUseCase":
            findings.append(f"reference to ResolveProposalUseCase at line {node.lineno}")
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value == "APPROVED"
        ):
            findings.append(
                f'assignment of "APPROVED" literal at line {node.lineno}'
            )
    return findings


def test_only_interrupt_approval_activates_a_plan():
    """Only ``_interrupt_for_approval`` may activate a plan.

    An AST walk of every top-level function AND module-scope statement in
    ``graph/`` and ``agents/`` (except ``state_helpers.py``, which owns the
    primitives) forbids the four routes a node could take to activate a
    plan without the resumed, human-driven interrupt path:

      (a) ``return approve_plan(state)``
      (b) ``state["approval"] = "APPROVED"``
      (c) ``ResolveProposalUseCase().execute(..., ApprovalStatus.APPROVED, ...)``
      (d) ``PostgresPlanRepository(conn).activate(...)``

    The one exception is ``_interrupt_for_approval`` inside
    ``initial_planning_graph.py``: that node runs after ``interrupt()``
    resumes with a human decision, and it is the sole legal caller of
    ``approve_plan`` and ``ResolveProposalUseCase`` in the orchestration
    layer (invariant: the LLM never activates a plan).
    """
    offenders: list[str] = []
    saw_privileged = False

    for path in _iter_source_files():
        module = ast.parse(path.read_text())

        module_findings = _find_approval_violations(_module_scope_statements(module))
        for finding in module_findings:
            offenders.append(f"{path.name}:<module>: {finding}")

        for func, name in _top_level_functions(module):
            privileged = (
                path.name == _APPROVAL_PRIVILEGED_MODULE
                and name == _APPROVAL_PRIVILEGED_FUNCTION
            )
            findings = _find_approval_violations(func)
            if privileged:
                saw_privileged = True
                assert findings, (
                    f"{_APPROVAL_PRIVILEGED_FUNCTION} must still contain the "
                    "approval activation path; found none"
                )
                continue
            for finding in findings:
                offenders.append(f"{path.name}:{name}: {finding}")

    assert saw_privileged, (
        f"{_APPROVAL_PRIVILEGED_FUNCTION} not found in {_APPROVAL_PRIVILEGED_MODULE}"
    )
    assert not offenders, (
        "plan-activation code found outside "
        f"{_APPROVAL_PRIVILEGED_FUNCTION}:\n" + "\n".join(offenders)
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
