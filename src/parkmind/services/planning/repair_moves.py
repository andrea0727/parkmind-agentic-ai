"""
Repair moves mapping for the Planner re-solve loop.

§21 [C23]
"""

from enum import Enum
from pydantic import BaseModel

from parkmind.core.contracts.enums import RuleId


class RepairAction(str, Enum):
    FORBID_NODE = "FORBID_NODE"
    RELAX_LUNCH_WINDOW = "RELAX_LUNCH_WINDOW"
    RELAX_WALKING_BUDGET = "RELAX_WALKING_BUDGET"
    RELOAD_CONTEXT = "RELOAD_CONTEXT"
    FAIL_CLOSED = "FAIL_CLOSED"


class PlannerRepairMove(BaseModel):
    action: RepairAction
    target_id: str | None = None


def get_repair_move(rule_id: RuleId, stop_id: str | None = None) -> PlannerRepairMove:
    """
    Map a RuleId to a deterministic repair move.
    """
    if rule_id == RuleId.OPENING_HOURS:
        return PlannerRepairMove(action=RepairAction.FORBID_NODE, target_id=stop_id)
        
    if rule_id == RuleId.SHOW_ARRIVAL:
        return PlannerRepairMove(action=RepairAction.FORBID_NODE, target_id=stop_id)
        
    if rule_id == RuleId.LUNCH_WINDOW:
        return PlannerRepairMove(action=RepairAction.RELAX_LUNCH_WINDOW)
        
    if rule_id == RuleId.WALKING_BUDGET:
        return PlannerRepairMove(action=RepairAction.RELAX_WALKING_BUDGET)
        
    if rule_id == RuleId.DATA_FRESHNESS:
        return PlannerRepairMove(action=RepairAction.RELOAD_CONTEXT)

    # MUST_DO, AVOID, DEPARTURE, HEIGHT, ACCESSIBILITY, RIDE_RESTRICTION
    return PlannerRepairMove(action=RepairAction.FAIL_CLOSED)
