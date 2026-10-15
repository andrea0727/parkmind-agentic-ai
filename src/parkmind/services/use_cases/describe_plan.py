"""Use case: what a plan's stops are called, for showing a plan or a proposal to the guests.

A ``Plan`` and a ``PlanDiff`` only carry node ids. The catalog gives them names; the base
plan of a replan gives the diff something to compare the candidate with.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from parkmind.core.contracts import Attraction, Plan
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    open_deps,
)


@dataclass(frozen=True)
class PlanDescription:
    plan: Plan
    base_plan: Plan | None
    attractions: Mapping[str, Attraction]


class DescribePlanUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(self, plan: Plan, base_plan_id: str | None = None) -> PlanDescription:
        with open_deps(self._deps_factory) as deps:
            catalog = load_catalog(deps)
            base = deps.plans.get(base_plan_id) if base_plan_id else None
        return PlanDescription(plan, base, {a.node_id: a for a in catalog})
