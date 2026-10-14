"""Use case: a PROVISIONAL plan for the next park day, offered once today's park has closed.

It is a preview, not a plan: never proposed, never approved, never saved as the active
plan, and the guests are told so. Live planning keeps its rules (rule 11 still refuses
stale data). The preview builds its own context for the opening moment of tomorrow: every
catalog attraction is assumed OPERATING and the waits come from the forecast chain's
historical profile, else the catalog's typical wait.

The guests' confirmed constraints are reused, moved to tomorrow keeping their park-local
clock times, so "leave at 20:00" stays "leave at 20:00".
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from parkmind.core.contracts import (
    PARK_TZ,
    AttractionStatus,
    CheckResult,
    CoverageReport,
    GuestProfile,
    LiveContext,
    PartyConstraints,
    Plan,
    TimeWindow,
)
from parkmind.core.language import DEFAULT_LANGUAGE, Language, text
from parkmind.services.use_cases.build_plan import BuildPlanUseCase
from parkmind.services.use_cases.explain_plan import ExplainPlanUseCase
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    PlanningUnavailableError,
    default_planning_deps,
    load_catalog,
    load_schedule,
    open_deps,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProvisionalPlan:
    text: str
    plan: Plan | None
    """``None`` when no plan could be built; ``text`` then says so."""
    target_date: date
    opens_at: datetime | None = None
    """When the park opens on ``target_date``; ``None`` when no plan could be built."""


def _on_date(moment: datetime, day: date) -> datetime:
    local = moment.astimezone(PARK_TZ)
    return local.replace(year=day.year, month=day.month, day=day.day)


def retimed(constraints: PartyConstraints, day: date) -> PartyConstraints:
    update: dict[str, object] = {"departure_time": _on_date(constraints.departure_time, day)}
    window = constraints.lunch_window
    if window is not None:
        update["lunch_window"] = TimeWindow(
            start=_on_date(window.start, day), end=_on_date(window.end, day)
        )
    return constraints.model_copy(update=update)


class ProvisionalPlanUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(
        self,
        *,
        thread_id: str,
        constraints: PartyConstraints,
        profiles: Sequence[GuestProfile],
        accessibility_ref: Sequence[str],
        now: datetime,
        lang: Language = DEFAULT_LANGUAGE,
    ) -> ProvisionalPlan:
        solo = len(constraints.guests) == 1
        target = now.astimezone(PARK_TZ).date() + timedelta(days=1)
        try:
            with open_deps(self._deps_factory) as deps:
                park = load_schedule(deps, target)
                catalog = load_catalog(deps)
        except PlanningUnavailableError:
            logger.warning("no schedule for the provisional plan", exc_info=True)
            return ProvisionalPlan(text("provisional_unavailable", lang, solo=solo), None, target)

        opening = park.opening_time
        context = LiveContext(
            snapshot_id=f"provisional-{target.isoformat()}",
            retrieved_at=opening,
            statuses={a.node_id: AttractionStatus.OPERATING for a in catalog},
            coverage=CoverageReport(
                required_attractions_covered=True,
                required_shows_covered=True,
                weather_covered=True,
                accessibility_checks_complete=True,
            ),
        )
        planned = retimed(constraints, target)
        builder = BuildPlanUseCase(self._deps_factory)
        objective = builder.resolve_group(
            thread_id=thread_id,
            constraints=planned,
            profiles=profiles,
            accessibility_ref=accessibility_ref,
            live_context=context,
        )
        built = builder.build(
            thread_id=thread_id,
            constraints=planned,
            profiles=profiles,
            accessibility_ref=accessibility_ref,
            live_context=context,
            objective=objective,
            now=opening,
        )
        if not built.resolved:
            return ProvisionalPlan(text("provisional_failed", lang, solo=solo), None, target)

        prepared = ExplainPlanUseCase(self._deps_factory).prepare(
            built.plan,
            CheckResult(valid=True),
            guests=planned.guests,
            must_do=planned.must_do,
            objective=objective,
            lang=lang,
        )
        opens = f"{opening.astimezone(PARK_TZ):%H:%M}"
        body = "\n".join(
            [
                text("provisional_header", lang, opening=opens),
                "",
                prepared.template,
                "",
                text("provisional_footer", lang, solo=solo),
            ]
        )
        return ProvisionalPlan(body, built.plan, target, opening)
