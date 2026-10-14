"""Use case: REPLAN the rest of the day for the active plan (P0-23, P0-34).

Composes the deterministic core exactly as ``BuildPlanUseCase`` does for the initial
plan -- ``ParkGraph``, ``PreferenceScorer``, ``GreedyInsertionOptimizer`` and the
section 21 re-solve loop -- and hands them to the ``Replanner``, so the graph reaches
none of them directly (.importlinter).

Like the initial plan, a replan reads the party's ``AccessibilityRequirements`` from
the SessionStore by ``accessibility_ref`` on every call (never from state, never
returned [C19]) and refuses to be fail-open: a guest without a stored record is simply
absent here and the checker (rule 11) rejects the plan.

The result is only a *candidate*. Nothing is persisted here: ``CheckPlanUseCase``
decides whether it may be proposed and ``ProposePlanUseCase`` stores it.

Two things the graph's state cannot hold are derived here from the repositories:

* **Progress.** The party's execution state is read from the repository. When it
  records no completed or skipped stop (nobody reported progress), the party is
  assumed to be on schedule: stops that should have ended by ``now`` count as done and
  are frozen, so the replan neither re-sends the party to them nor forgets what they
  walked. ``as_of`` never lies before ``now``. That assumption is made again on every
  replan, from the plan being replanned and the clock: it is never stored, so a later
  replan never inherits an earlier, stale one (``ResolveProposalUseCase`` carries over
  only what was reported, on approval).
* **Reported availability.** A report the thread recorded after the context's
  snapshot (for example a guest saying a ride is closed) is applied to the context's
  statuses, so the replan and the CHECK that follows see the same attractions as
  unavailable, until a newer snapshot says otherwise.

A caller without the graph's thread -- ``planner.replan`` (P0-27), whose clients
send the plan, its progress and the events inline (section 31) -- passes them in
``ReplanRequest.execution_state`` and ``ReplanRequest.events``: the given progress
replaces the repository's (the on-schedule assumption still applies to it), and the
given events are applied on top of the ones the thread recorded, whatever their
time (the recorded ones only when newer than the snapshot). The graph leaves
both at their defaults.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from parkmind.core.contracts import (
    AttractionStatus,
    Event,
    EventType,
    GroupObjective,
    GuestProfile,
    LiveContext,
    PartyConstraints,
    Plan,
    PlanExecutionState,
)
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.planning.errors import ContextReloadError
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.planning.replanner import Replanner, ReplanResult
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
from parkmind.services.use_cases.score_preferences import ScorePreferencesUseCase

logger = logging.getLogger(__name__)


ProgressSource = Literal["sent", "stored", "assumed"]


def _reports_stops(state: PlanExecutionState | None) -> bool:
    return state is not None and bool(
        state.completed_stop_ids or state.skipped_stop_ids
    )


def check_progress_of_plan(plan: Plan, state: PlanExecutionState) -> None:
    """Progress must be for this plan and only about its stops: an unknown stop id
    would also switch off the on-schedule assumption without saying so."""
    if state.plan_id != plan.plan_id:
        raise ValueError(
            f"execution_state is for plan {state.plan_id}, not {plan.plan_id}"
        )
    stops = {s.node_id for s in plan.stops}
    if not {*state.completed_stop_ids, *state.skipped_stop_ids} <= stops:
        raise ValueError("completed and skipped stops must be stops of the plan")


def progress_source(state: PlanExecutionState | None, *, sent: bool) -> ProgressSource:
    """Where the completed stops come from, as ``effective_execution_state`` takes them.

    ``assumed`` also covers a state that was sent or stored without completed or skipped
    stops: its location and walking are kept, but the stops done are the on-schedule ones.
    """
    if not _reports_stops(state):
        return "assumed"
    return "sent" if sent else "stored"


def effective_execution_state(
    plan: Plan, stored: PlanExecutionState | None, now: datetime
) -> PlanExecutionState:
    """The progress to replan from: what is stored, else an on-schedule party."""
    state = stored or PlanExecutionState(plan_id=plan.plan_id, as_of=now)
    if not _reports_stops(state):
        done = [stop.node_id for stop in plan.stops if stop.departure_time <= now]
        state = state.model_copy(update={"completed_stop_ids": done})
    return state.model_copy(update={"as_of": max(state.as_of, now)})


def with_event_statuses(
    context: LiveContext, events: Iterable[Event], *, newer_only: bool = True
) -> LiveContext:
    """``context`` with the availability reports newer than its snapshot applied.

    ``newer_only=False`` applies them whatever their time: the events a caller sends
    with a replan say what happened for that replan, and a client's clock may lag
    the snapshot's (a closure stamped a minute early must not be dropped silently).
    """
    statuses = dict(context.statuses)
    for event in sorted(events, key=lambda e: (e.timestamp, e.event_id)):
        if event.attraction_id is None:
            continue
        if newer_only and event.timestamp <= context.retrieved_at:
            continue
        if event.type is EventType.ATTRACTION_DOWN:
            statuses[event.attraction_id] = AttractionStatus.DOWN
        elif event.type is EventType.ATTRACTION_AVAILABLE:
            statuses[event.attraction_id] = AttractionStatus.OPERATING
    if statuses == context.statuses:
        return context
    return context.model_copy(update={"statuses": statuses})


@dataclass(frozen=True)
class ReplanRequest:
    thread_id: str
    plan: Plan
    """The active plan being replanned."""
    constraints: PartyConstraints
    profiles: Sequence[GuestProfile]
    accessibility_ref: Sequence[str]
    live_context: LiveContext
    objective: GroupObjective
    now: datetime
    rest_first: bool = False
    """A confirmed GUEST_FATIGUE: open the new horizon with a REST stop (section 25)."""
    execution_state: PlanExecutionState | None = None
    """The plan's progress, sent by a caller without the thread; ``None``: the repository's."""
    events: Sequence[Event] = ()
    """Events to apply on top of the ones the thread recorded (a caller without the thread)."""

    def __post_init__(self) -> None:
        if self.execution_state is not None:
            check_progress_of_plan(self.plan, self.execution_state)


@dataclass(frozen=True)
class ReplanOutcome:
    result: ReplanResult
    live_context: LiveContext
    """What to keep in state: no per-guest accessibility results [C19]."""
    execution_state: PlanExecutionState
    """The progress the replan started from (see the module docstring); the CHECK uses it too."""
    progress_source: ProgressSource = "stored"
    """Where that progress came from: the caller, the repository, or the on-schedule assumption."""


class ReplanUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(self, request: ReplanRequest) -> ReplanOutcome:
        reloaded: list[LiveContext] = []
        events: list[Event] = []

        def reload_context() -> LiveContext:
            try:
                loaded = LoadContextUseCase(self._deps_factory).execute(
                    request.thread_id, request.accessibility_ref, request.now
                )
            except PlanningUnavailableError as exc:
                raise ContextReloadError(str(exc)) from exc
            reloaded.append(with_events(loaded.for_state()))
            return with_events(loaded.live_context)

        def with_events(context: LiveContext) -> LiveContext:
            recorded = with_event_statuses(context, events)
            return with_event_statuses(recorded, request.events, newer_only=False)

        with open_deps(self._deps_factory) as deps:
            catalog = load_catalog(deps)
            park = load_park(deps, request.now)
            requirements, _ = load_requirements(
                deps, request.thread_id, request.accessibility_ref
            )
            events.extend(deps.events.list_for_thread(request.thread_id))
            stored = (
                request.execution_state
                if request.execution_state is not None
                else deps.execution_states.get(request.plan.plan_id)
            )
            execution_state = effective_execution_state(request.plan, stored, request.now)
            source = progress_source(stored, sent=request.execution_state is not None)
            live_context = with_checks(
                with_events(request.live_context),
                party_checks(requirements, catalog, deps.knowledge),
            )
            park_graph = ParkGraph.from_sources(
                routing=deps.routing, park=park, attractions=catalog, live_context=live_context
            )
            scores = ScorePreferencesUseCase().execute(
                objective=request.objective,
                profiles=request.profiles,
                attractions=catalog,
                live_context=live_context,
                knowledge=deps.knowledge,
                park_graph=park_graph,
            )
            forecast = build_forecast_service(
                deps.snapshots, deps.id_mappings, now=execution_state.as_of
            )
            replanner = Replanner(
                PlannerResolveLoop(GreedyInsertionOptimizer(park_graph=park_graph), ConstraintChecker()),
                park_graph,
            )
            result = replanner.replan(
                current_plan=request.plan,
                execution_state=execution_state,
                context=live_context,
                constraints=request.constraints,
                utilities=scores.utilities(),
                park=park,
                catalog=catalog,
                accessibility_reqs=requirements,
                group_objective=request.objective,
                forecast_service=forecast,
                scores=scores,
                rest_first=request.rest_first,
                context_reloader=reload_context,
            )
            state_context: LiveContext = (
                reloaded[-1]
                if reloaded
                else without_guests(live_context, request.accessibility_ref)
            )
        if not result.valid:
            logger.info("replan of %s found no valid plan", request.plan.plan_id)
        return ReplanOutcome(result, state_context, execution_state, source)


__all__: list[Any] = [
    "ReplanOutcome",
    "ReplanRequest",
    "ReplanUseCase",
    "check_progress_of_plan",
    "effective_execution_state",
    "with_event_statuses",
]
