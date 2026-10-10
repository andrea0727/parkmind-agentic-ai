"""
Planner Re-solve Loop orchestrator.

Handles the iterative repair of plans that fail the ConstraintChecker.
§21 [C23]

Architectural Deviations / Approximations:
- SHIFT_OR_FORBID_NEIGHBOR: Approximated by forbidding the neighbor rather than shifting the window.
- HEIGHT / RIDE_RESTRICTION / MUST_DO: Implemented as fail-closed for safety and deterministic constraint guarantees.
"""

import logging
from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import datetime

from pydantic import Field

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    GroupObjective,
    LiveContext,
    Park,
    ParkMindBaseModel,
    PartyConstraints,
    Plan,
    Provenance,
    RuleId,
    StopKind,
)
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.planning.errors import ContextReloadError
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.repair_moves import RepairAction, get_repair_move

logger = logging.getLogger(__name__)


class PlannerResolveResult(ParkMindBaseModel):
    """
    Result of the repair loop after applying architecture §21 moves.

    §21 [C23]
    """

    valid: bool
    plan: Plan | None = None
    unmet_must_do: list[str] = Field(default_factory=list)
    fatal_error: str | None = None


class PlannerResolveLoop:
    """
    Executes the deterministic cycle that sends failed plans back to the
    planner with a single mapped repair move per attempt.
    """

    def __init__(
        self,
        optimizer: GreedyInsertionOptimizer,
        checker: ConstraintChecker,
        max_attempts: int = 3,
    ) -> None:
        self._optimizer = optimizer
        self._checker = checker
        self._max_attempts = max_attempts

    def resolve(
        self,
        constraints: PartyConstraints,
        context: LiveContext,
        utilities: dict[str, float],
        now: datetime,
        park: Park,
        accessibility_reqs: Sequence[AccessibilityRequirements] | None = None,
        catalog: Sequence[Attraction] | None = None,
        restaurant_node_ids: Sequence[str] | None = None,
        provenance: Provenance | None = None,
        group_objective: GroupObjective | None = None,
        context_reloader: Callable[[], LiveContext] | None = None,
    ) -> PlannerResolveResult:
        """
        Run the repair loop up to max_attempts.

        If DATA_FRESHNESS fails, the context_reloader is invoked once.
        Returns a PlannerResolveResult. Absolute invariant: valid=True is ONLY
        returned when ConstraintChecker produces a clean valid CheckResult.
        """
        current_constraints = deepcopy(constraints)
        current_context = context

        catalog_index = {a.node_id: a for a in catalog} if catalog else {}

        for attempt in range(self._max_attempts):
            # 1. Generate candidate plan
            plan = self._optimizer.build_plan(
                constraints=current_constraints,
                context=current_context,
                utilities=utilities,
                accessibility_reqs=accessibility_reqs,
                park=park,
                catalog=catalog,
                restaurant_node_ids=restaurant_node_ids,
                provenance=provenance,
                group_objective=group_objective,
            )

            # 2. Check candidate plan
            check_result = self._checker.check(
                plan=plan,
                constraints=current_constraints,
                accessibility=list(accessibility_reqs) if accessibility_reqs else [],
                attractions=catalog_index,
                park=park,
                live_context=current_context,
                now=now,
            )

            # 3. If clean check, return success
            if check_result.valid:
                return PlannerResolveResult(
                    valid=True,
                    plan=plan,
                    unmet_must_do=plan.unmet_must_do,
                    fatal_error=None,
                )

            # DATA_FRESHNESS handling
            freshness_violation = next(
                (v for v in check_result.violations if v.rule == RuleId.DATA_FRESHNESS),
                None,
            )
            if freshness_violation is not None:
                if context_reloader is None:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error="Stale data: DATA_FRESHNESS violation but no context_reloader provided.",
                    )
                try:
                    current_context = context_reloader()
                except ContextReloadError as e:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error=f"Context reload failed: {e}",
                    )
                # Consume reloader (single attempt)
                context_reloader = None

                # Re-check without calling optimizer again
                recheck_result = self._checker.check(
                    plan=plan,
                    constraints=current_constraints,
                    accessibility=list(accessibility_reqs)
                    if accessibility_reqs
                    else [],
                    attractions=catalog_index,
                    park=park,
                    live_context=current_context,
                    now=now,
                )
                if recheck_result.valid:
                    return PlannerResolveResult(
                        valid=True,
                        plan=plan,
                        unmet_must_do=plan.unmet_must_do,
                        fatal_error=None,
                    )
                still_stale = any(
                    v.rule == RuleId.DATA_FRESHNESS for v in recheck_result.violations
                )
                if still_stale:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error="Stale data: Live context remains stale after context reload.",
                    )
                check_result = recheck_result

            # 4. Map the first violation to a repair move (§21 SSOT)
            violation = check_result.violations[0]
            repair_move = get_repair_move(violation.rule, violation.stop_id)

            logger.info(
                f"Resolve loop attempt {attempt + 1}: violation {violation.rule.value} -> action {repair_move.action.value}"
            )

            # Rule 4 / Must-Do Violation Handling
            if violation.rule == RuleId.MUST_DO:
                target_detail = violation.stop_id or violation.message or "unknown"
                return PlannerResolveResult(
                    valid=False,
                    plan=None,
                    unmet_must_do=[],
                    fatal_error=f"Infeasible constraint set: MUST_DO attraction '{target_detail}' is OPERATING but could not be validly scheduled.",
                )

            # FORBID_NODE (OPENING_HOURS, AVOID)
            if repair_move.action == RepairAction.FORBID_NODE:
                target_node = repair_move.target_id
                if not target_node:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error=f"FORBID_NODE missing target_id for violation {violation.rule.value}.",
                    )
                if target_node in current_constraints.must_do:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error=f"Infeasible constraint set: cannot forbid MUST_DO node '{target_node}'.",
                    )
                if target_node not in current_constraints.avoid:
                    current_constraints.avoid.append(target_node)
                continue

            # SHIFT_OR_FORBID_NEIGHBOR (SHOW_ARRIVAL, LUNCH_WINDOW, DEPARTURE)
            if repair_move.action == RepairAction.SHIFT_OR_FORBID_NEIGHBOR:
                neighbor_to_remove = None
                window_node = repair_move.target_id

                stop_idx = None
                if window_node:
                    for idx, s in enumerate(plan.stops):
                        if s.node_id == window_node:
                            stop_idx = idx
                            break

                if stop_idx is not None and stop_idx > 0:
                    prev_stop = plan.stops[stop_idx - 1]
                    if (
                        prev_stop.kind == StopKind.ATTRACTION
                        and prev_stop.node_id not in current_constraints.must_do
                    ):
                        neighbor_to_remove = prev_stop.node_id
                elif stop_idx is not None and stop_idx < len(plan.stops) - 1:
                    next_stop = plan.stops[stop_idx + 1]
                    if (
                        next_stop.kind == StopKind.ATTRACTION
                        and next_stop.node_id not in current_constraints.must_do
                    ):
                        neighbor_to_remove = next_stop.node_id
                elif violation.rule == RuleId.DEPARTURE and plan.stops:
                    last_attr = next(
                        (
                            s.node_id
                            for s in reversed(plan.stops)
                            if s.kind == StopKind.ATTRACTION
                            and s.node_id not in current_constraints.must_do
                        ),
                        None,
                    )
                    neighbor_to_remove = last_attr
                elif window_node and window_node not in current_constraints.must_do:
                    neighbor_to_remove = window_node

                if neighbor_to_remove:
                    if neighbor_to_remove not in current_constraints.avoid:
                        current_constraints.avoid.append(neighbor_to_remove)
                    continue

                return PlannerResolveResult(
                    valid=False,
                    plan=None,
                    unmet_must_do=[],
                    fatal_error=f"Infeasible constraint set: {violation.rule.value} - {violation.message}",
                )

            # DROP_LOWEST_UTILITY_OPTIONAL (WALKING_BUDGET, ACCESSIBILITY)
            if repair_move.action == RepairAction.DROP_LOWEST_UTILITY_OPTIONAL:
                optional_stops = [
                    s
                    for s in plan.stops
                    if s.kind == StopKind.ATTRACTION
                    and s.node_id not in current_constraints.must_do
                ]
                if optional_stops:
                    lowest_utility_stop = min(optional_stops, key=lambda s: s.utility)
                    if lowest_utility_stop.node_id not in current_constraints.avoid:
                        current_constraints.avoid.append(lowest_utility_stop.node_id)
                    continue

                return PlannerResolveResult(
                    valid=False,
                    plan=None,
                    unmet_must_do=[],
                    fatal_error=f"Infeasible constraint set: {violation.rule.value} - {violation.message}",
                )

            # Terminal fail-closed actions (HEIGHT, RIDE_RESTRICTION, etc.)
            return PlannerResolveResult(
                valid=False,
                plan=None,
                unmet_must_do=[],
                fatal_error=f"Infeasible constraint set: {violation.rule.value} - {violation.message}",
            )

        # Max attempts reached without convergence
        return PlannerResolveResult(
            valid=False,
            plan=None,
            unmet_must_do=[],
            fatal_error=f"Failed to find valid plan after {self._max_attempts} attempts.",
        )
