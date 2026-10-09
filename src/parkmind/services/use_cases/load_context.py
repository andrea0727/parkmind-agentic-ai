"""LOAD CONTEXT -- the deterministic loader (P0-30; the agentic loader is P1-22).

Source order (section 43): a fresh collection (when a collector is configured and
the provider answers) -> the latest valid stored snapshot (P0-11) -> nothing, which
is ``ContextUnavailableError``. The historical-profile rung of the chain feeds the
forecast, not the context, so it is not a source here.

A collected snapshot's coverage is park-wide and leaves
``accessibility_checks_complete`` false on purpose. This use case completes it per
party: every guest with requirements on file is checked against every catalog
attraction with ``check_accessibility`` (P0-26a). The flag turns true only when
each pair got a result and every guest the confirmation committed requirements
for could be read back; otherwise the gap is named in ``coverage_gaps`` and rule 11
refuses the plan.

``AccessibilityRequirements`` are read here, used, and dropped: only the derived
``AccessibilityCheck`` results reach the returned ``LiveContext`` [C19]. This is
the first step of the graph that reads the session store.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from parkmind.core.contracts import AccessibilityCheck, LiveContext
from parkmind.services.clients.themeparks_errors import ThemeParksClientError
from parkmind.services.ports import RepositoryError
from parkmind.services.use_cases.check_accessibility import check_accessibility
from parkmind.services.use_cases.latest_snapshot import (
    DEFAULT_MAX_AGE,
    latest_valid_snapshot,
)
from parkmind.services.use_cases.planning_deps import (
    ContextUnavailableError,
    DepsFactory,
    PlanningDeps,
    default_planning_deps,
    load_catalog,
    load_requirements,
)
from parkmind.services.use_cases.snapshot_normalization import ACCESSIBILITY_GAP

__all__ = ["ContextUnavailableError", "LoadContextUseCase", "LoadedContext"]

ContextSource = Literal["live", "snapshot"]


@dataclass(frozen=True)
class LoadedContext:
    live_context: LiveContext
    source: ContextSource
    """``live`` when this call collected (or found this window's) snapshot; ``snapshot`` for the fallback."""
    age: timedelta
    """At ``now``; rule 11 judges freshness from the snapshot itself."""


class LoadContextUseCase:
    def __init__(
        self,
        deps_factory: DepsFactory = default_planning_deps,
        *,
        max_age: timedelta = DEFAULT_MAX_AGE,
    ) -> None:
        self._deps_factory = deps_factory
        self._max_age = max_age

    def execute(
        self, thread_id: str, accessibility_ref: Sequence[str], now: datetime
    ) -> LoadedContext:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        with self._deps_factory() as deps:
            base, source = self._base_context(deps, now)
            catalog = load_catalog(deps)
            requirements, missing = load_requirements(deps, thread_id, accessibility_ref)
            checks = [
                check_accessibility(req, attraction.node_id, deps.knowledge)
                for req in requirements
                for attraction in catalog
            ]
            live_context = _with_accessibility(base, checks, requirements_missing=missing)
        return LoadedContext(live_context, source, now - live_context.retrieved_at)

    def _base_context(self, deps: PlanningDeps, now: datetime) -> tuple[LiveContext, ContextSource]:
        if deps.collector is not None:
            try:
                result = deps.collector.collect(now=now)
                live = result.live_context or deps.snapshots.get(result.snapshot_id)
                if live is not None:
                    return live, "live"
            except (ThemeParksClientError, RepositoryError):
                pass  # fall back to the stored snapshot (section 43)
        latest = latest_valid_snapshot(deps.snapshots, now=now, max_age=self._max_age)
        if latest is None:
            raise ContextUnavailableError("no live data and no valid stored snapshot")
        return latest.live_context, "snapshot"


def _with_accessibility(
    base: LiveContext,
    checks: Sequence[AccessibilityCheck],
    *,
    requirements_missing: Sequence[str],
) -> LiveContext:
    checked_guests = {c.guest_id for c in checks}
    kept = [r for r in base.accessibility_results if r.guest_id not in checked_guests]
    gaps = [g for g in base.coverage.coverage_gaps if g != ACCESSIBILITY_GAP]
    for guest_id in requirements_missing:
        gaps.append(f"accessibility requirements of guest {guest_id} could not be read")
    complete = not requirements_missing
    coverage = base.coverage.model_copy(
        update={"accessibility_checks_complete": complete, "coverage_gaps": gaps}
    )
    return base.model_copy(
        update={"accessibility_results": [*kept, *checks], "coverage": coverage}
    )
