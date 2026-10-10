"""
Replan Use Case (P0-23).

Orchestrates the replanning workflow: loading current state from repositories, 
delegating to the deterministic Replanner service, and persisting the new plan.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from parkmind.core.contracts import (
    Attraction,
    Event,
    LiveContext,
    Park,
    PartyConstraints,
)
from parkmind.services.planning.replanner import Replanner, ReplanResult
from parkmind.services.ports.execution_state_repository import ExecutionStateRepository
from parkmind.services.ports.plan_repository import PlanRepository


@dataclass
class ReplanRequest:
    """Input payload for the replan use case."""
    thread_id: str
    plan_id: str
    context: LiveContext
    constraints: PartyConstraints
    utilities: dict[str, float]
    park: Park
    catalog: Sequence[Attraction]
    event: Event | None = None


class ReplanUseCase:
    """
    Coordinates the retrieval of current plan state, execution of the 
    re-optimization logic, and persistence of the resulting plan.
    """

    def __init__(
        self,
        replanner: Replanner,
        plan_repo: PlanRepository,
        state_repo: ExecutionStateRepository,
    ) -> None:
        self._replanner = replanner
        self._plan_repo = plan_repo
        self._state_repo = state_repo

    def execute(self, request: ReplanRequest) -> ReplanResult:
        # 1. Load current plan and execution state from the database
        current_plan = self._plan_repo.get(request.plan_id)
        if not current_plan:
            return ReplanResult(
                valid=False,
                plan=None,
                diff=None,
                unmet_must_do=[],
                fatal_error=f"Plan '{request.plan_id}' not found.",
            )

        execution_state = self._state_repo.get(request.plan_id)
        if not execution_state:
            return ReplanResult(
                valid=False,
                plan=None,
                diff=None,
                unmet_must_do=[],
                fatal_error=f"Execution state for plan '{request.plan_id}' not found.",
            )

        # 2. Delegate re-optimization to the deterministic domain service
        result = self._replanner.replan(
            current_plan=current_plan,
            execution_state=execution_state,
            context=request.context,
            constraints=request.constraints,
            utilities=request.utilities,
            park=request.park,
            catalog=request.catalog,
            event=request.event,
        )

        # 3. Persist the new plan independently (P0-12)
        if result.valid and result.plan:
            self._plan_repo.save(request.thread_id, result.plan)

        return result