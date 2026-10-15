"""Use case: the thread's active plan, the one a replan starts from.

The plan repository is the only source of truth for what is active: graph state
never holds an active plan of its own to trust (``current_plan`` is written only
when a human approves, section 23).
"""

from parkmind.core.contracts import Plan
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    open_deps,
)


class ActivePlanUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(self, thread_id: str) -> Plan | None:
        with open_deps(self._deps_factory) as deps:
            return deps.plans.get_active(thread_id)
