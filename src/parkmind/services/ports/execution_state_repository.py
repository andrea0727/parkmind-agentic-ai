"""ExecutionStateRepository -- ``PlanExecutionState`` (section 33 [C13]).

Feeds BEHIND_SCHEDULE detection, replanner stop-preservation and the simulator.
"""

from typing import Protocol

from parkmind.core.contracts import PlanExecutionState


class ExecutionStateRepository(Protocol):
    def save(self, state: PlanExecutionState) -> bool:
        """Store the latest state of ``state.plan_id``.

        Returns ``False`` and keeps the stored state when it is newer
        (``as_of``) than ``state``, so a delayed writer cannot roll progress
        back. Raises ``NotFoundError`` if the plan was not saved.
        """
        ...

    def get(self, plan_id: str) -> PlanExecutionState | None: ...
