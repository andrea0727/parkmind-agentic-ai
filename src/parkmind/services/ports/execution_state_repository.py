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

    def lock(self, plan_id: str) -> None:
        """Hold ``plan_id``'s state for the rest of the caller's transaction.

        A writer that reads the state, changes it and saves it calls this first, in the
        same transaction (``PlanningDeps.transaction``): a second writer then waits and
        reads what the first one saved, instead of overwriting it with a change of its
        own made to the older state. Raises ``NotFoundError`` if the plan was not saved.
        """
        ...
