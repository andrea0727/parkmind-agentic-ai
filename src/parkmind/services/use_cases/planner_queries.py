"""Use case: the planner reads behind the ``planner.*`` tools (P0-27; Architecture section 31).

``build_plan`` runs the same chain the initial planning graph runs -- LOAD
CONTEXT, RESOLVE GROUP, BUILD PLAN (with the section 21 re-solve loop), CHECK --
by calling the same use cases, and stops there: nothing is proposed, persisted
or activated (section 27 [C23]: no MCP tool can activate or mutate a plan). The
result is a *candidate* with its check. ``check_plan``, ``score_preferences``
and ``forecast_waits`` expose one step each.

Accessibility is passed by reference only [C19]: ``session_id`` (the SessionStore
key, the graph's ``thread_id``) and the ``guest_ids`` with requirements on file,
read here as the graph reads them. A server in another process sees only
``persisted`` records; ``session_only`` ones are missing there, so the context's
coverage is incomplete and the check fails closed (rule 11).

What leaves this use case never carries a requirement value: the checker's
messages for rules 9 and 10 (walking, rest, heat; ride restrictions) quote the
guest's limits, so those violations, and a re-solve failure that quotes them,
are replaced by fixed sentences that keep the rule id and the stop.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from parkmind.core.contracts import (
    CheckResult,
    ConstraintViolation,
    GroupObjective,
    GuestProfile,
    LiveContext,
    PartyConstraints,
    Plan,
    RuleId,
)
from parkmind.services.personalization.preference_scorer import PreferenceScores
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import WaitForecast
from parkmind.services.use_cases.build_plan import BuildPlanUseCase
from parkmind.services.use_cases.check_plan import CheckPlanUseCase
from parkmind.services.use_cases.forecast import build_forecast_service
from parkmind.services.use_cases.latest_snapshot import DEFAULT_MAX_AGE
from parkmind.services.use_cases.load_context import ContextSource, LoadContextUseCase
from parkmind.services.use_cases.party_accessibility import party_checks, with_checks
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    load_park,
    load_requirements,
    open_deps,
)
from parkmind.services.use_cases.score_preferences import ScorePreferencesUseCase

_PRIVATE_RULES: dict[RuleId, tuple[str, str]] = {
    RuleId.ACCESSIBILITY: (
        (
            "A guest's accessibility requirement (walking, rest or heat) is not met at "
            "this stop; the details stay in the guest's session."
        ),
        "Drop an optional stop, insert a REST stop, or prefer an indoor alternative.",
    ),
    RuleId.RIDE_RESTRICTION: (
        (
            "A guest's ride restriction conflicts with this stop's safety notice, or no "
            "notice is on file; the details stay in the guest's session."
        ),
        "Remove the guest from this stop's served guests, or forbid the stop.",
    ),
}
_PRIVATE_FAILURE = re.compile(
    rf"^(Infeasible constraint set: ({'|'.join(r.value for r in _PRIVATE_RULES)})) - .*$",
    re.DOTALL,
)


def public_violation(violation: ConstraintViolation) -> ConstraintViolation:
    """The violation as it may leave the system: no requirement value in it."""
    if violation.rule not in _PRIVATE_RULES:
        return violation
    message, suggestion = _PRIVATE_RULES[violation.rule]
    return violation.model_copy(update={"message": message, "suggestion": suggestion})


def public_check(result: CheckResult) -> CheckResult:
    return result.model_copy(
        update={"violations": [public_violation(v) for v in result.violations]}
    )


def public_failure(failure: str | None) -> str | None:
    if failure is None:
        return None
    return _PRIVATE_FAILURE.sub(r"\1 (details stay in the guest's session)", failure)


@dataclass(frozen=True)
class ContextStamp:
    """The park data a planner answer was computed from."""

    snapshot_id: str
    retrieved_at: datetime
    age: timedelta
    source: ContextSource
    stale: bool
    """Outside the window rule 11 trusts (``0 <= age <= 30 min``)."""


@dataclass(frozen=True)
class PlanCandidate:
    plan: Plan
    check: CheckResult
    resolved: bool
    """``True`` when the re-solve loop reached a plan the checker passes."""
    failure: str | None
    """Why the re-solve loop found no valid plan (redacted), when it did not."""
    context: ContextStamp


@dataclass(frozen=True)
class CheckAnswer:
    check: CheckResult
    context: ContextStamp


@dataclass(frozen=True)
class ScoreAnswer:
    objective: GroupObjective
    scores: PreferenceScores
    context: ContextStamp


@dataclass(frozen=True)
class ForecastAnswer:
    forecasts: list[WaitForecast]
    no_reading: list[tuple[str, datetime]]
    """``(attraction_id, at)`` pairs no strategy could forecast: never invented."""
    unknown_ids: list[str]


def _stamp(
    live_context: LiveContext, source: ContextSource, now: datetime
) -> ContextStamp:
    age = now - live_context.retrieved_at
    return ContextStamp(
        snapshot_id=live_context.snapshot_id,
        retrieved_at=live_context.retrieved_at,
        age=age,
        source=source,
        stale=not (timedelta(0) <= age <= DEFAULT_MAX_AGE),
    )


class PlannerQueries:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory
        self._load = LoadContextUseCase(deps_factory)
        self._build = BuildPlanUseCase(deps_factory)
        self._check = CheckPlanUseCase(deps_factory)

    def build_plan(
        self,
        *,
        session_id: str,
        constraints: PartyConstraints,
        profiles: Sequence[GuestProfile],
        guest_ids: Sequence[str],
        now: datetime,
    ) -> PlanCandidate:
        loaded = self._load.execute(session_id, guest_ids, now)
        context = loaded.for_state()
        objective = self._build.resolve_group(
            thread_id=session_id,
            constraints=constraints,
            profiles=profiles,
            accessibility_ref=guest_ids,
            live_context=context,
        )
        built = self._build.build(
            thread_id=session_id,
            constraints=constraints,
            profiles=profiles,
            accessibility_ref=guest_ids,
            live_context=context,
            objective=objective,
            now=now,
        )
        check = self._check.execute(
            thread_id=session_id,
            plan=built.plan,
            constraints=constraints,
            accessibility_ref=guest_ids,
            live_context=built.live_context,
            now=now,
        )
        return PlanCandidate(
            plan=built.plan,
            check=public_check(check),
            resolved=built.resolved,
            failure=public_failure(built.failure),
            context=_stamp(built.live_context, loaded.source, now),
        )

    def check_plan(
        self,
        *,
        session_id: str,
        plan: Plan,
        constraints: PartyConstraints,
        guest_ids: Sequence[str],
        now: datetime,
    ) -> CheckAnswer:
        loaded = self._load.execute(session_id, guest_ids, now)
        context = loaded.for_state()
        check = self._check.execute(
            thread_id=session_id,
            plan=plan,
            constraints=constraints,
            accessibility_ref=guest_ids,
            live_context=context,
            now=now,
        )
        return CheckAnswer(public_check(check), _stamp(context, loaded.source, now))

    def score_preferences(
        self,
        *,
        session_id: str,
        constraints: PartyConstraints,
        profiles: Sequence[GuestProfile],
        guest_ids: Sequence[str],
        now: datetime,
    ) -> ScoreAnswer:
        """The party's objective and utilities, scored over the context BUILD PLAN uses."""
        loaded = self._load.execute(session_id, guest_ids, now)
        context = loaded.for_state()
        objective = self._build.resolve_group(
            thread_id=session_id,
            constraints=constraints,
            profiles=profiles,
            accessibility_ref=guest_ids,
            live_context=context,
        )
        with open_deps(self._deps_factory) as deps:
            catalog = load_catalog(deps)
            requirements, _ = load_requirements(deps, session_id, guest_ids)
            scored_context = with_checks(
                context, party_checks(requirements, catalog, deps.knowledge)
            )
            park_graph = ParkGraph.from_sources(
                routing=deps.routing,
                park=load_park(deps, now),
                attractions=catalog,
                live_context=scored_context,
            )
            scores = ScorePreferencesUseCase().execute(
                objective=objective,
                profiles=profiles,
                attractions=catalog,
                live_context=scored_context,
                knowledge=deps.knowledge,
                park_graph=park_graph,
            )
        return ScoreAnswer(objective, scores, _stamp(context, loaded.source, now))

    def forecast_waits(
        self, attraction_ids: Sequence[str], at: Sequence[datetime], now: datetime
    ) -> ForecastAnswer:
        with open_deps(self._deps_factory) as deps:
            known = {a.node_id for a in load_catalog(deps)}
            service = build_forecast_service(deps.snapshots, deps.id_mappings, now=now)
            ids = list(dict.fromkeys(attraction_ids))
            forecasts: list[WaitForecast] = []
            no_reading: list[tuple[str, datetime]] = []
            for attraction_id in (i for i in ids if i in known):
                for when in at:
                    forecast = service.forecast_wait(attraction_id, when, now=now)
                    if forecast is None:
                        no_reading.append((attraction_id, when))
                    else:
                        forecasts.append(forecast)
        return ForecastAnswer(forecasts, no_reading, [i for i in ids if i not in known])
