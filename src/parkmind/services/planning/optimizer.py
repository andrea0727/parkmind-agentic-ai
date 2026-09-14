"""
Optimizer — builds a candidate Plan given constraints, context and utility
scores. Month-1 scope: GreedyInsertionOptimizer only.
"""

from parkmind.models.plan import Plan


class GreedyInsertionOptimizer:
    def build_plan(self, constraints, context, utilities) -> Plan:
        raise NotImplementedError
