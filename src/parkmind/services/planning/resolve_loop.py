"""
Planner Re-solve Loop orchestrator.

Handles the iterative repair of plans that fail the ConstraintChecker.
§21 [C23]
"""

import logging
from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import datetime, timedelta

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    GroupObjective,
    LiveContext,
    Park,
    PartyConstraints,
    Provenance,
)
from parkmind.core.contracts.base import PlannerResolveResult
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.planning.errors import ContextReloadError
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.repair_moves import RepairAction, get_repair_move

logger = logging.getLogger(__name__)


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
        accessibility_reqs: Sequence[AccessibilityRequirements] | None = None,
        park: Park | None = None,
        catalog: Sequence[Attraction] | None = None,
        restaurant_node_ids: Sequence[str] | None = None,
        provenance: Provenance | None = None,
        group_objective: GroupObjective | None = None,
        context_reloader: Callable[[], LiveContext] | None = None,
    ) -> PlannerResolveResult:
        """
        Run the repair loop up to max_attempts.

        If DATA_FRESHNESS fails, the context_reloader is invoked once.
        Returns a PlannerResolveResult.
        """
        current_constraints = deepcopy(constraints)
        current_context = context

        catalog_index = {a.node_id: a for a in catalog} if catalog else {}

        for attempt in range(self._max_attempts):
            # 1. Generate plan
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

            # 2. Check plan
            check_result = self._checker.check(
                plan=plan,
                constraints=current_constraints,
                accessibility=list(accessibility_reqs) if accessibility_reqs else [],
                attractions=catalog_index,
                park=park,  # type: ignore[arg-type]
                live_context=current_context,
                now=now,
            )

            # 3. If valid, return success
            if check_result.valid:
                return PlannerResolveResult(
                    valid=True,
                    plan=plan,
                    unmet_must_do=plan.unmet_must_do,
                    fatal_error=None,
                )

            # 4. If invalid, map the first violation to a repair move
            violation = check_result.violations[0]
            repair_move = get_repair_move(violation.rule, violation.stop_id)

            logger.info(
                f"Resolve loop attempt {attempt + 1}: violation {violation.rule.value}, "
                f"executing repair {repair_move.action.value}"
            )

            # 5. Apply the repair move
            if repair_move.action == RepairAction.FAIL_CLOSED:
                return PlannerResolveResult(
                    valid=False,
                    plan=None,
                    unmet_must_do=[],
                    fatal_error=f"Infeasible constraint set: {violation.rule.value} - {violation.message}",
                )

            if repair_move.action == RepairAction.RELOAD_CONTEXT:
                if context_reloader is None:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error="DATA_FRESHNESS violation but no context_reloader provided.",
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
                # Disable context reloading for future attempts to fail closed [C23]
                context_reloader = None
                continue

            if repair_move.action == RepairAction.FORBID_NODE:
                if not repair_move.target_id:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error="FORBID_NODE missing target_id.",
                    )
                current_constraints.avoid.append(repair_move.target_id)
                continue

            if repair_move.action == RepairAction.RELAX_LUNCH_WINDOW:
                if not current_constraints.lunch_window:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error="Cannot relax lunch window: not set.",
                    )
                # Expand by 15 mins both sides
                new_start = current_constraints.lunch_window.start - timedelta(minutes=15)
                new_end = current_constraints.lunch_window.end + timedelta(minutes=15)
                current_constraints.lunch_window = current_constraints.lunch_window.model_copy(
                    update={"start": new_start, "end": new_end}
                )
                continue

            if repair_move.action == RepairAction.RELAX_WALKING_BUDGET:
                if current_constraints.party_walking_budget_minutes is None:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error="Cannot relax walking budget: not set.",
                    )
                # Increase by 20%
                current_budget = current_constraints.party_walking_budget_minutes
                current_constraints.party_walking_budget_minutes = int(current_budget * 1.2)
                continue

        # If max attempts reached
        return PlannerResolveResult(
            valid=False,
            plan=None,
            unmet_must_do=[],
            fatal_error=f"Failed to find valid plan after {self._max_attempts} attempts.",
        )
