"""Use case: validate a candidate plan with the deterministic ConstraintChecker.

Its own use case because it is the gate in front of EXPLAIN and PROPOSE and is
also exposed as its own tool. Requirements are read from the SessionStore per
call and never returned [C19].
"""

from collections.abc import Sequence
from datetime import datetime

from parkmind.core.contracts import CheckResult, LiveContext, PartyConstraints, Plan
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    load_park,
    load_requirements,
)


class CheckPlanUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(
        self,
        *,
        thread_id: str,
        plan: Plan,
        constraints: PartyConstraints,
        accessibility_ref: Sequence[str],
        live_context: LiveContext,
        now: datetime,
    ) -> CheckResult:
        with self._deps_factory() as deps:
            catalog = load_catalog(deps)
            park = load_park(deps, now)
            requirements, _ = load_requirements(deps, thread_id, accessibility_ref)
            return ConstraintChecker().check(
                plan,
                constraints,
                requirements,
                {a.node_id: a for a in catalog},
                park,
                live_context,
                now,
            )
