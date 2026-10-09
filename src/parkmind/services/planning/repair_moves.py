"""
Planning repair move definitions.

Architecture §21 (Repair Move Table).
"""

from enum import Enum

from pydantic import BaseModel

from parkmind.core.contracts.enums import RuleId


class RepairAction(str, Enum):
    """Action to take when a rule is violated (Architecture §21)."""

    FAIL_CLOSED = "FAIL_CLOSED"
    RELOAD_CONTEXT = "RELOAD_CONTEXT"
    FORBID_NODE = "FORBID_NODE"
    SHIFT_OR_FORBID_NEIGHBOR = "SHIFT_OR_FORBID_NEIGHBOR"
    DROP_LOWEST_UTILITY_OPTIONAL = "DROP_LOWEST_UTILITY_OPTIONAL"


class RepairMove(BaseModel):
    """Specification of an action to repair a violated rule."""

    action: RepairAction
    target_id: str | None = None


# Architecture §21 Mapping Table: exactly one deterministic move per canonical rule id
RULE_TO_REPAIR_ACTION: dict[RuleId, RepairAction] = {
    # Node removals / exclusions
    RuleId.OPENING_HOURS: RepairAction.FORBID_NODE,
    RuleId.AVOID: RepairAction.FORBID_NODE,
    # Fixed window neighbor exclusions / shifts
    RuleId.SHOW_ARRIVAL: RepairAction.SHIFT_OR_FORBID_NEIGHBOR,
    RuleId.LUNCH_WINDOW: RepairAction.SHIFT_OR_FORBID_NEIGHBOR,
    RuleId.DEPARTURE: RepairAction.SHIFT_OR_FORBID_NEIGHBOR,
    # Budget / capacity trimming (drop lowest utility optional)
    RuleId.WALKING_BUDGET: RepairAction.DROP_LOWEST_UTILITY_OPTIONAL,
    RuleId.ACCESSIBILITY: RepairAction.DROP_LOWEST_UTILITY_OPTIONAL,
    # Context reload (handled once, then fails closed)
    RuleId.DATA_FRESHNESS: RepairAction.RELOAD_CONTEXT,
    # Deterministic terminal / fail-closed rules
    RuleId.HEIGHT: RepairAction.FAIL_CLOSED,
    RuleId.RIDE_RESTRICTION: RepairAction.FAIL_CLOSED,
    RuleId.MUST_DO: RepairAction.FAIL_CLOSED,
}


def get_repair_move(rule_id: RuleId, stop_id: str | None = None) -> RepairMove:
    """
    Get the deterministic repair move for a given rule violation.
    """
    action = RULE_TO_REPAIR_ACTION.get(rule_id, RepairAction.FAIL_CLOSED)
    return RepairMove(action=action, target_id=stop_id)