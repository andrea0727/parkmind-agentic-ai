"""
Replanner — recomputes a plan after a significant event, minimizing
changes to what the guest already approved.
"""

from parkmind.models.event import Event
from parkmind.models.plan import Plan


class Replanner:
    def replan(self, current_plan: Plan, event: Event, remaining_constraints, context) -> Plan:
        raise NotImplementedError
