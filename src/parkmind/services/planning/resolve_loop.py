"""
Planner Re-solve Loop orchestrator.

Handles the iterative repair of plans that fail the ConstraintChecker.
§21 [C23]
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
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer

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
        # [Punto 3] NUNCA mutar o relajar PartyConstraints
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

            # [Punto 6] DATA_FRESHNESS:
            # Recargar LiveContext una sola vez y hacer re-check. No duplicar llamadas al optimizador.
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
                except Exception as e:  # noqa: BLE001
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error=f"Context reload failed: {e}",
                    )
                # Recargar una sola vez
                context_reloader = None

                # Re-check sin volver a llamar al optimizador
                recheck_result = self._checker.check(
                    plan=plan,
                    constraints=current_constraints,
                    accessibility=list(accessibility_reqs) if accessibility_reqs else [],
                    attractions=catalog_index,
                    park=park,  # type: ignore[arg-type]
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
                        fatal_error="Stale data: DATA_FRESHNESS violation but no context_reloader provided.",
                    )
                check_result = recheck_result

            # 4. Map the first violation to a repair move
            violation = check_result.violations[0]

            logger.info(
                f"Resolve loop attempt {attempt + 1}: violation {violation.rule.value}"
            )

            # [Punto 4] MUST_DO:
            # Si no se puede insertar o la atracción está DOWN todo el día,
            # registrar el ID en unmet_must_do y retornar un plan válido (valid=True, unmet_must_do=[...]).
            if violation.rule == RuleId.MUST_DO:
                missing_must_dos = [
                    m
                    for m in current_constraints.must_do
                    if m not in {s.node_id for s in plan.stops}
                ]
                unmet_list = list(
                    dict.fromkeys(list(plan.unmet_must_do) + missing_must_dos)
                )
                updated_plan = plan.model_copy(update={"unmet_must_do": unmet_list})
                return PlannerResolveResult(
                    valid=True,
                    plan=updated_plan,
                    unmet_must_do=unmet_list,
                    fatal_error=None,
                )

            # [Punto 4] OPENING_HOURS / AVOID:
            # Aplicar FORBID_NODE (agregar el nodo/atracción a la lista de nodos prohibidos) y volver a intentar en el siguiente loop.
            if violation.rule in (RuleId.OPENING_HOURS, RuleId.AVOID):
                target_node = violation.stop_id
                if not target_node:
                    return PlannerResolveResult(
                        valid=False,
                        plan=None,
                        unmet_must_do=[],
                        fatal_error=f"FORBID_NODE missing target_id for violation {violation.rule.value}.",
                    )
                if target_node not in current_constraints.avoid:
                    current_constraints.avoid.append(target_node)
                continue

            # [Punto 4] SHOW_ARRIVAL, LUNCH_WINDOW, DEPARTURE:
            # Fijar la parada de ventana y reinsertar las vecinas.
            if violation.rule in (
                RuleId.SHOW_ARRIVAL,
                RuleId.LUNCH_WINDOW,
                RuleId.DEPARTURE,
            ):
                neighbor_to_remove = None
                window_node = violation.stop_id

                stop_idx = None
                if window_node:
                    for idx, s in enumerate(plan.stops):
                        if s.node_id == window_node:
                            stop_idx = idx
                            break

                if stop_idx is not None and stop_idx > 0:
                    prev_stop = plan.stops[stop_idx - 1]
                    if prev_stop.kind == StopKind.ATTRACTION:
                        neighbor_to_remove = prev_stop.node_id
                elif stop_idx is not None and stop_idx < len(plan.stops) - 1:
                    next_stop = plan.stops[stop_idx + 1]
                    if next_stop.kind == StopKind.ATTRACTION:
                        neighbor_to_remove = next_stop.node_id
                elif violation.rule == RuleId.DEPARTURE and plan.stops:
                    last_attr = next(
                        (
                            s.node_id
                            for s in reversed(plan.stops)
                            if s.kind == StopKind.ATTRACTION
                        ),
                        None,
                    )
                    neighbor_to_remove = last_attr
                elif window_node:
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

            # [Punto 4] WALKING_BUDGET / ACCESSIBILITY:
            # Quitar la parada opcional de menor utilidad o insertar un descanso (REST).
            if violation.rule in (RuleId.WALKING_BUDGET, RuleId.ACCESSIBILITY):
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

                # No hay paradas opcionales para quitar; inviable determinísticamente
                return PlannerResolveResult(
                    valid=False,
                    plan=None,
                    unmet_must_do=[],
                    fatal_error=f"Infeasible constraint set: {violation.rule.value} - {violation.message}",
                )

            # Restricciones físicas y de seguridad (HEIGHT, RIDE_RESTRICTION, etc.): fail closed
            return PlannerResolveResult(
                valid=False,
                plan=None,
                unmet_must_do=[],
                fatal_error=f"Infeasible constraint set: {violation.rule.value} - {violation.message}",
            )

        # Si se superan los intentos máximos sin converger
        return PlannerResolveResult(
            valid=False,
            plan=None,
            unmet_must_do=[],
            fatal_error=f"Failed to find valid plan after {self._max_attempts} attempts.",
        )
