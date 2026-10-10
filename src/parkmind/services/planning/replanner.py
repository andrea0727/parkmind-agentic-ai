"""
Replanner Service (P0-23).

Handles the deterministic re-optimization of a guest's itinerary 
mid-execution, based on their PlanExecutionState and current LiveContext.
Enforces P0-12 (immutable plan IDs) and P0-22 (PlanDiffModel generation).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from parkmind.core.contracts import (
    Attraction,
    Event,
    LiveContext,
    Park,
    PartyConstraints,
    Plan,
    PlanExecutionState,
)
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.planning.plan_diff import PlanDiffModel, diff_plans
from parkmind.services.planning.resolve_loop import PlannerResolveLoop


@dataclass
class ReplanResult:
    """Standardized output for the replanning operation."""
    valid: bool
    plan: Plan | None
    diff: PlanDiffModel | None
    unmet_must_do: list[str]
    fatal_error: str | None = None


class Replanner:
    """
    Orchestrates partial plan re-optimization, preserving completed stops
    and delegating the remaining horizon to the PlannerResolveLoop.
    """

    def __init__(self, resolve_loop: PlannerResolveLoop, park_graph: ParkGraph) -> None:
        self._resolve_loop = resolve_loop
        self._park_graph = park_graph

    def replan(
        self,
        current_plan: Plan,
        execution_state: PlanExecutionState,
        context: LiveContext,
        constraints: PartyConstraints,
        utilities: dict[str, float],
        park: Park,
        catalog: Sequence[Attraction],
        event: Event | None = None,
    ) -> ReplanResult:
        
        # 1. Segment state: isolate completed stops to freeze them
        completed_stop_ids = set(execution_state.completed_stop_ids)
        completed_stops = [s for s in current_plan.stops if s.node_id in completed_stop_ids]

        # 2. Determine exact start location and time for the remaining horizon
        as_of = execution_state.as_of
        start_location_id = execution_state.current_location_node_id
        if not start_location_id and completed_stops:
            # Fallback to the last completed stop's node if no explicit location is tracked
            start_location_id = completed_stops[-1].node_id

        # 3. Adjust constraints for the future horizon
        # (We don't need the optimizer to schedule must-dos that are already done)
        remaining_must_do = [
            node_id for node_id in constraints.must_do 
            if node_id not in completed_stop_ids
        ]
        
        adjusted_constraints = constraints.model_copy(
            update={"must_do": remaining_must_do}
        )

        # 4. Resolve the remaining horizon using the robust §21 loop
        resolve_result = self._resolve_loop.resolve(
            constraints=adjusted_constraints,
            context=context,
            utilities=utilities,
            now=as_of,
            park=park,
            catalog=catalog,
            # We pass the start location down to the optimizer via kwargs
            start_location_node_id=start_location_id,
        )

        # If the remaining horizon is mathematically infeasible, fail closed safely
        if not resolve_result.valid or not resolve_result.plan:
            return ReplanResult(
                valid=False,
                plan=None,
                diff=None,
                unmet_must_do=resolve_result.unmet_must_do,
                fatal_error=resolve_result.fatal_error or "Failed to re-optimize remaining horizon.",
            )

        # 5. Assemble the new aggregate Plan (P0-12)
        new_stops = completed_stops + resolve_result.plan.stops
        new_plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        
        new_provenance = current_plan.provenance.model_copy(
            update={
                "snapshot_id": context.snapshot_id,
                "retrieved_at": context.retrieved_at,
            }
        )

        new_plan = current_plan.model_copy(
            update={
                "plan_id": new_plan_id,
                "stops": new_stops,
                "version": 1,
                "provenance": new_provenance,
                "total_wait_minutes": sum(s.expected_wait_minutes for s in new_stops),
                "total_walking_minutes": sum(s.walking_minutes for s in new_stops),
                "unmet_must_do": resolve_result.unmet_must_do,
            }
        )

        # 6. Calculate the delta for the UI / Concierge (P0-22)
        diff = diff_plans(old_plan=current_plan, new_plan=new_plan)

        return ReplanResult(
            valid=True,
            plan=new_plan,
            diff=diff,
            unmet_must_do=resolve_result.unmet_must_do,
        )
