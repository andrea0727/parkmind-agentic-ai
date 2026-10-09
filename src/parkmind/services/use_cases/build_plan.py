"""Use case: RESOLVE GROUP and BUILD PLAN of the initial planning graph (P0-30).

Composes the deterministic core -- ``ParkGraph``, ``GroupPreferenceResolver``,
``PreferenceScorer`` and ``GreedyInsertionOptimizer`` -- so the graph reaches
none of them directly (.importlinter). Two public steps because the graph has
one node for each.

``AccessibilityRequirements`` are read from the SessionStore by ``thread_id`` on
every call and handed to the core; they are never returned [C19]. A guest whose
record is missing is simply absent here: ``load_context`` already marked that
gap in the coverage report, and the checker (rule 11) refuses the plan.

The result is only a *candidate*. ``CheckPlanUseCase`` decides whether it may be
proposed.
"""

from collections.abc import Sequence
from datetime import datetime

from parkmind.core.contracts import (
    FairnessConfig,
    GroupObjective,
    GuestProfile,
    LiveContext,
    PartyConstraints,
    Plan,
)
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.use_cases.forecast import build_forecast_service
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    load_park,
    load_requirements,
)
from parkmind.services.use_cases.resolve_group_preferences import (
    ResolveGroupPreferencesUseCase,
)
from parkmind.services.use_cases.score_preferences import ScorePreferencesUseCase

DEFAULT_FAIRNESS = FairnessConfig(lambda_fairness=0.3, min_satisfaction_floor=0.0)


class BuildPlanUseCase:
    def __init__(
        self,
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
        with self._deps_factory() as deps:
            catalog = load_catalog(deps)
            requirements, _ = load_requirements(deps, thread_id, accessibility_ref)
            return ResolveGroupPreferencesUseCase().execute(
                guests=list(constraints.guests),
                profiles=list(profiles),
                accessibility=requirements,
                attractions=catalog,
                party_constraints=constraints,
                live_context=live_context,
                fairness=self._fairness,
            )

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
    ) -> Plan:
        with self._deps_factory() as deps:
            catalog = load_catalog(deps)
            park = load_park(deps, now)
            requirements, _ = load_requirements(deps, thread_id, accessibility_ref)
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
            return GreedyInsertionOptimizer(park_graph=park_graph).build_plan(
                constraints=constraints,
                context=live_context,
                utilities=scores.utilities(),
                accessibility_reqs=requirements,
                park=park,
                catalog=catalog,
                group_objective=objective,
                forecast_service=forecast,
                now=now,
                scores=scores,
            )
