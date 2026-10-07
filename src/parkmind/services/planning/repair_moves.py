"""
Planning repair move definitions.

Architecture §21 (Repair Move Table).
"""

from enum import Enum

from pydantic import BaseModel

from parkmind.core.contracts.enums import RuleId


class RepairAction(str, Enum):
    """Action to take when a rule is violated."""

    FAIL_CLOSED = "FAIL_CLOSED"
    RELOAD_CONTEXT = "RELOAD_CONTEXT"
    FORBID_NODE = "FORBID_NODE"
    RELAX_LUNCH_WINDOW = "RELAX_LUNCH_WINDOW"
    RELAX_WALKING_BUDGET = "RELAX_WALKING_BUDGET"


class RepairMove(BaseModel):
    """Specification of an action to repair a violated rule."""

    action: RepairAction
    target_id: str | None = None


# Architecture §21 Mapping Table: exactly one deterministic move per canonical rule id
RULE_TO_REPAIR_ACTION: dict[RuleId, RepairAction] = {
    # Relaxes
    RuleId.WALKING_BUDGET: RepairAction.RELAX_WALKING_BUDGET,
    RuleId.LUNCH_WINDOW: RepairAction.RELAX_LUNCH_WINDOW,
    # Node removals / exclusions
    RuleId.OPENING_HOURS: RepairAction.FORBID_NODE,
    RuleId.SHOW_ARRIVAL: RepairAction.FORBID_NODE,
    # Context reload (handled once, then fails closed [C23])
    RuleId.DATA_FRESHNESS: RepairAction.RELOAD_CONTEXT,
    # Deterministic terminal / fail-closed rules (safety & physical feasibility)
    RuleId.HEIGHT: RepairAction.FAIL_CLOSED,
    RuleId.ACCESSIBILITY: RepairAction.FAIL_CLOSED,
    RuleId.RIDE_RESTRICTION: RepairAction.FAIL_CLOSED,
    RuleId.MUST_DO: RepairAction.FAIL_CLOSED,
    RuleId.AVOID: RepairAction.FAIL_CLOSED,
    RuleId.DEPARTURE: RepairAction.FAIL_CLOSED,
}


def get_repair_move(rule_id: RuleId, stop_id: str | None = None) -> RepairMove:
    """
    Get the deterministic repair move for a given rule violation.
    """
    action = RULE_TO_REPAIR_ACTION.get(rule_id, RepairAction.FAIL_CLOSED)
    return RepairMove(action=action, target_id=stop_id)
