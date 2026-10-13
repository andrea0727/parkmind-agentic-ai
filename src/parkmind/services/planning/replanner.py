"""
Replanner Service (P0-23).

Handles the deterministic re-optimization of a guest's itinerary
mid-execution, based on their PlanExecutionState and current LiveContext.
Enforces P0-12 (immutable plan IDs) and P0-22 (PlanDiffModel generation).

A replan is returned valid only when the whole day (completed stops + new horizon)
passes the same ConstraintChecker rules as an initial plan: the day's walking limits
count what was already walked, and the fatigue cap bounds the new horizon.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    CheckResult,
    GroupObjective,
    LiveContext,
    Park,
    PartyConstraints,
    Plan,
    PlanExecutionState,
    StopKind,
)
from parkmind.services.personalization.preference_scorer import PreferenceScores
from parkmind.services.planning.forecast_service import ForecastService
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
    check: CheckResult | None = None
    """The whole day's check (the final gate), when the horizon got that far: its
    violations keep their rule ids and stops, which ``fatal_error`` joins into text."""


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
        accessibility_reqs: Sequence[AccessibilityRequirements] | None = None,
        group_objective: GroupObjective | None = None,
        forecast_service: ForecastService | None = None,
        scores: PreferenceScores | None = None,
        rest_first: bool = False,
        context_reloader: Callable[[], LiveContext] | None = None,
    ) -> ReplanResult:
        """Replan the rest of the day; ``rest_first`` opens the new horizon with a REST stop.

        ``rest_first`` is a confirmed GUEST_FATIGUE (section 25): the party rests before
        walking on. If a rest cannot be scheduled the replan fails closed.
        """
        # 1. Segment state: isolate completed stops to freeze them
        completed_stop_ids = set(execution_state.completed_stop_ids)
        completed_stops = [s for s in current_plan.stops if s.node_id in completed_stop_ids]

        # 2. Determine exact start location and time for the remaining horizon
        as_of = execution_state.as_of
        start_location_id = execution_state.current_location_node_id
        if not start_location_id and completed_stops:
            # Fallback to the last completed stop's node if no explicit location is tracked
            start_location_id = completed_stops[-1].node_id

        walking_done_minutes = sum(s.walking_minutes for s in completed_stops)

        # 3. Adjust constraints for the future horizon
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
            start_location_node_id=start_location_id,
            accessibility_reqs=accessibility_reqs,
            group_objective=group_objective,
            forecast_service=forecast_service,
            scores=scores,
            execution_state=execution_state,
            completed_node_ids=list(completed_stop_ids),
            walking_done_minutes=walking_done_minutes,
            rest_first=rest_first,
            context_reloader=context_reloader,
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

        if rest_first and (
            not resolve_result.plan.stops or resolve_result.plan.stops[0].kind != StopKind.REST
        ):
            return ReplanResult(
                valid=False,
                plan=None,
                diff=None,
                unmet_must_do=[],
                fatal_error="Could not schedule a rest before the rest of the day.",
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

        # Final gate: the whole day, not just the horizon, must pass the checker.
        full_check = self._resolve_loop.checker.check_replan(
            plan=new_plan,
            constraints=constraints,
            accessibility=list(accessibility_reqs) if accessibility_reqs else [],
            attractions={a.node_id: a for a in catalog},
            park=park,
            live_context=context,
            now=as_of,
            execution_state=execution_state,
        )
        if not full_check.valid:
            reasons = "; ".join(v.message for v in full_check.violations)
            return ReplanResult(
                valid=False,
                plan=None,
                diff=None,
                unmet_must_do=[],
                fatal_error=f"The replanned day does not pass the constraint checker: {reasons}",
                check=full_check,
            )

        # 6. Calculate the delta for the UI / Concierge (P0-22)
        diff = diff_plans(old_plan=current_plan, new_plan=new_plan)

        return ReplanResult(
            valid=True,
            plan=new_plan,
            diff=diff,
            unmet_must_do=resolve_result.unmet_must_do,
            check=full_check,
        )