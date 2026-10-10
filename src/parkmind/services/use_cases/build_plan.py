"""Use case: RESOLVE GROUP and BUILD PLAN of the initial planning graph (P0-30).

Composes the deterministic core -- ``ParkGraph``, ``GroupPreferenceResolver``,
``PreferenceScorer`` and ``GreedyInsertionOptimizer`` -- so the graph reaches
none of them directly (.importlinter). Two public steps because the graph has
one node for each.

``AccessibilityRequirements`` are read from the SessionStore by ``thread_id`` on
every call and handed to the core; they are never returned [C19]; the party's accessibility results are re-derived here
rather than read from the checkpointed context. A guest whose
record is missing is simply absent here: ``load_context`` already marked that
gap in the coverage report, and the checker (rule 11) refuses the plan.

The result is only a *candidate*. ``CheckPlanUseCase`` decides whether it may be
proposed.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from parkmind.core.contracts import (
    FairnessConfig,
    GroupObjective,
    GuestProfile,
    LiveContext,
    PartyConstraints,
    Plan,
)
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.planning.errors import ContextReloadError
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.planning.resolve_loop import PlannerResolveLoop
from parkmind.services.use_cases.forecast import build_forecast_service
from parkmind.services.use_cases.load_context import LoadContextUseCase
from parkmind.services.use_cases.party_accessibility import (
    party_checks,
    with_checks,
    without_guests,
)
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    PlanningUnavailableError,
    default_planning_deps,
    load_catalog,
    load_park,
    load_requirements,
    open_deps,
)
from parkmind.services.use_cases.resolve_group_preferences import (
    ResolveGroupPreferencesUseCase,
)
from parkmind.services.use_cases.score_preferences import ScorePreferencesUseCase

logger = logging.getLogger(__name__)

DEFAULT_FAIRNESS = FairnessConfig(lambda_fairness=0.3, min_satisfaction_floor=0.0)


def _without_accessibility_limits(objective: GroupObjective) -> GroupObjective:
    """Clear the per-guest limits the resolver copies from the requirements [C19].

    The objective is checkpointed. The optimizer and the checker read the limits
    from the requirements, never from these fields.
    """
    hard = objective.hard_constraints.model_copy(
        update={
            "per_guest_daily_walking_limits": {},
            "per_guest_rest_frequency": {},
            "ride_restrictions": {},
        }
    )
    return objective.model_copy(update={"hard_constraints": hard})


@dataclass(frozen=True)
class BuiltPlan:
    plan: Plan
    live_context: LiveContext
    """What to keep in state: the reloaded context when the loop had to reload it [C19]."""
    resolved: bool
    """False when the re-solve loop found no valid plan; ``plan`` is then the unrepaired candidate."""
    failure: str | None = None


class BuildPlanUseCase:
    def __init__(
        self,
        park_graph,
        preference_scorer,
        optimizer,
        constraint_checker,
        group_preference_resolver,
        forecast_service,
    ):
        self.park_graph = park_graph
        self.preference_scorer = preference_scorer
        self.optimizer = optimizer
        self.constraint_checker = constraint_checker
        self.group_preference_resolver = group_preference_resolver
        self.forecast_service = forecast_service

    def execute(self, constraints, guest_profiles, live_context):
        raise NotImplementedError
        deps_factory: DepsFactory = default_planning_deps,
        *,
        fairness: FairnessConfig = DEFAULT_FAIRNESS,
    ) -> None:
        self._deps_factory = deps_factory
        self._fairness = fairness

    def resolve_group(
        self,
        *,
        thread_id: str,
        constraints: PartyConstraints,
        profiles: Sequence[GuestProfile],
        accessibility_ref: Sequence[str],
        live_context: LiveContext,
    ) -> GroupObjective:
        with open_deps(self._deps_factory) as deps:
            catalog = load_catalog(deps)
            requirements, _ = load_requirements(deps, thread_id, accessibility_ref)
            objective = ResolveGroupPreferencesUseCase().execute(
                guests=list(constraints.guests),
                profiles=list(profiles),
                accessibility=requirements,
                attractions=catalog,
                party_constraints=constraints,
                live_context=with_checks(
                    live_context, party_checks(requirements, catalog, deps.knowledge)
                ),
                fairness=self._fairness,
            )
        return _without_accessibility_limits(objective)

    def build(
        self,
        *,
        thread_id: str,
        constraints: PartyConstraints,
        profiles: Sequence[GuestProfile],
        accessibility_ref: Sequence[str],
        live_context: LiveContext,
        objective: GroupObjective,
        now: datetime,
    ) -> BuiltPlan:
        """Build a plan with the re-solve loop (section 21): repair, reload stale data once.

        When the loop finds nothing valid the unrepaired candidate is returned with
        ``resolved=False``, so CHECK can name the violated rules for the user.
        """
        reloaded: list[LiveContext] = []

        def reload_context() -> LiveContext:
            try:
                loaded = LoadContextUseCase(self._deps_factory).execute(
                    thread_id, accessibility_ref, now
                )
            except PlanningUnavailableError as exc:
                raise ContextReloadError(str(exc)) from exc
            reloaded.append(loaded.for_state())
            return loaded.live_context

        with open_deps(self._deps_factory) as deps:
            catalog = load_catalog(deps)
            park = load_park(deps, now)
            requirements, _ = load_requirements(deps, thread_id, accessibility_ref)
            live_context = with_checks(
                live_context, party_checks(requirements, catalog, deps.knowledge)
            )
            park_graph = ParkGraph.from_sources(
                routing=deps.routing, park=park, attractions=catalog, live_context=live_context
            )
            scores = ScorePreferencesUseCase().execute(
                objective=objective,
                profiles=profiles,
                attractions=catalog,
                live_context=live_context,
                knowledge=deps.knowledge,
                park_graph=park_graph,
            )
            forecast = build_forecast_service(deps.snapshots, deps.id_mappings, now=now)
            optimizer = GreedyInsertionOptimizer(park_graph=park_graph)
            inputs: dict[str, Any] = {
                "constraints": constraints,
                "utilities": scores.utilities(),
                "accessibility_reqs": requirements,
                "park": park,
                "catalog": catalog,
                "group_objective": objective,
                "forecast_service": forecast,
                "now": now,
                "scores": scores,
            }
            result = PlannerResolveLoop(optimizer, ConstraintChecker()).resolve(
                context=live_context, context_reloader=reload_context, **inputs
            )
            state_context = (
                reloaded[-1] if reloaded else without_guests(live_context, accessibility_ref)
            )
            if result.valid and result.plan is not None:
                return BuiltPlan(result.plan, state_context, resolved=True)
            logger.info("re-solve loop found no valid plan: %s", result.fatal_error)
            candidate = optimizer.build_plan(context=live_context, **inputs)
            return BuiltPlan(candidate, state_context, resolved=False, failure=result.fatal_error)
