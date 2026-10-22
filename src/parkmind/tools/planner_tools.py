"""``planner.*`` tools.

``build_plan``, ``check_plan``, ``replan``, ``score_preferences``,
``forecast_waits``. Each handler is one call to ``PlannerQueries``, which
composes the same use cases the graph runs; no planner logic lives here.

None of these tools proposes, persists or activates a plan (section 27 [C23]):
``build_plan`` and ``replan`` return a *candidate* and its check, nothing else.
``replan`` takes the current plan, its progress and the events inline (section
31), so a client without a stored plan -- the LLM-only baseline (section 44) --
can replan; ``rest_first`` is the client's to set, since the event policy is not
run here.

Accessibility is passed by reference -- ``session_id`` plus ``guest_ids`` -- and
read from the SessionStore server-side [C19]; no requirement value is accepted or
returned, and ``replan`` returns the progress it started from without its walking
minutes (with the fatigue cap they would give a limit back). A residual risk
remains, as for ``check_plan``: a client that varies what it sends (walked minutes,
stops) and watches which answers fail can narrow a guest's limit down; and, as in
``build_plan``, a plan bounded by the guest's remaining walking budget is itself an
answer to the limit, without varying anything. What bounds
it is who can call at all: the server has no authentication and serves only this
machine (README, "Trust model").
They have no effect on guest, plan or proposal state, so they are read-only; the
LOAD CONTEXT allowlist still excludes them by namespace (section 8.1).

Inputs are capped well above a real party and day (``MAX_GUESTS``, ``MAX_STOPS``,
``MAX_EVENTS``, ``MAX_FORECASTS``), so one request cannot ask for unbounded work.
"""

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from parkmind.core.contracts import (
    CheckResult,
    Event,
    FairnessConfig,
    GuestProfile,
    PartyConstraints,
    Plan,
    PlanDiff,
    PlanExecutionState,
)
from parkmind.services.use_cases.planner_queries import ContextStamp, PlannerQueries
from parkmind.services.use_cases.replan import check_progress_of_plan
from parkmind.tools.contracts import ToolProvenance, ToolResult
from parkmind.tools.spec import ToolContext, ToolSpec

PLANNER_SOURCE = "parkmind_planner"
FORECAST_SOURCE = "parkmind_forecast"

MAX_GUESTS = 20
"""Guests per party, ``guest_ids`` and ``profiles`` per request."""
MAX_STOPS = 60
"""Stops in a plan sent to ``check_plan`` or ``replan`` (a park day is far fewer)."""
MAX_EVENTS = 20
"""Events sent with one ``replan`` request."""
MAX_FORECASTS = 500
"""Forecasts per ``forecast_waits`` request: attractions x arrival times."""

Id = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _PartyRequest(_Request):
    session_id: Id = Field(
        description=(
            "The conversation's session (thread) id: guests' accessibility requirements "
            "are read from it server-side, never sent."
        )
    )
    guest_ids: Annotated[list[Id], Field(max_length=MAX_GUESTS)] = Field(
        default_factory=list,
        description="Guests whose accessibility requirements are on file in that session.",
    )
    constraints: PartyConstraints
    now: AwareDatetime | None = Field(
        default=None,
        description="Planning time with its offset; omit for the server's clock (set it to replay a day).",
    )

    @field_validator("constraints")
    @classmethod
    def _party_size(cls, constraints: PartyConstraints) -> PartyConstraints:
        if len(constraints.guests) > MAX_GUESTS:
            raise ValueError(f"at most {MAX_GUESTS} guests")
        return constraints


# -- requests -------------------------------------------------------------------------


Profiles = Annotated[list[GuestProfile], Field(max_length=MAX_GUESTS)]


class BuildPlanRequest(_PartyRequest):
    profiles: Profiles = Field(default_factory=list)


class CheckPlanRequest(_PartyRequest):
    plan: Plan

    @field_validator("plan")
    @classmethod
    def _plan_size(cls, plan: Plan) -> Plan:
        if len(plan.stops) > MAX_STOPS:
            raise ValueError(f"at most {MAX_STOPS} stops")
        return plan


class ReplanToolRequest(CheckPlanRequest):
    """The current plan (``plan``), its progress and what happened (section 31)."""

    profiles: Profiles = Field(default_factory=list)
    execution_state: PlanExecutionState | None = Field(
        default=None,
        description=(
            "The plan's progress: completed or skipped stops, location, walking done. "
            "Omit it to use the progress stored for this plan. Either way, a stop that "
            "has already ended and isn't listed is assumed done (an on-schedule party): "
            "list a stop that wasn't done as skipped."
        ),
    )
    events: Annotated[list[Event], Field(max_length=MAX_EVENTS)] = Field(
        default_factory=list,
        description=(
            "What happened since the plan was made, applied on top of the events the "
            "session recorded: a closure or a reopening changes which rides are available. "
            "Whatever their timestamp, they override the snapshot's status of that ride "
            "(a closure sent here wins over a snapshot that says the ride reopened, and "
            "a reopening over one that says it is down). Only availability is read: "
            "severity and requires_replan are ignored."
        ),
    )
    rest_first: bool = Field(
        default=False,
        description="Open the new horizon with a rest (a confirmed guest fatigue report).",
    )

    @field_validator("events")
    @classmethod
    def _availability_reports_only(cls, events: list[Event]) -> list[Event]:
        """``severity`` and ``requires_replan`` are EventPolicy's to set [Invariant #4]: a
        client's are dropped, since a replan reads only availability from its events."""
        return [
            Event.model_validate(e.model_dump(exclude={"severity", "requires_replan"}))
            for e in events
        ]

    @field_validator("execution_state")
    @classmethod
    def _progress_of_this_plan(
        cls, state: PlanExecutionState | None, info: ValidationInfo
    ) -> PlanExecutionState | None:
        """The use case checks it too; here a wrong one is the ``execution_state`` field's."""
        plan = info.data.get("plan")
        if state is not None and plan is not None:
            check_progress_of_plan(plan, state)
        return state


class ScorePreferencesRequest(_PartyRequest):
    profiles: Profiles = Field(default_factory=list)
    top: int = Field(
        default=10, ge=1, le=60, description="How many attractions to list."
    )


class ForecastWaitsRequest(_Request):
    attraction_ids: Annotated[list[Id], Field(min_length=1, max_length=100)]
    at: Annotated[list[AwareDatetime], Field(max_length=48)] = Field(
        default_factory=list,
        description=(
            "Arrival times to forecast (with offset); omit for now. At most "
            f"{MAX_FORECASTS} forecasts in all: attraction_ids x at."
        ),
    )
    now: AwareDatetime | None = Field(
        default=None,
        description="Forecast as of this time, with its offset; omit for the server's clock "
        "(set it to replay a day).",
    )

    @field_validator("at")
    @classmethod
    def _bounded(cls, at: list[datetime], info: ValidationInfo) -> list[datetime]:
        """The total is attraction_ids x at; ``at`` is what pushes it over, so it is named."""
        ids = info.data.get("attraction_ids") or []
        if len(ids) * max(len(at), 1) > MAX_FORECASTS:
            raise ValueError(f"at most {MAX_FORECASTS} forecasts (attraction_ids x at)")
        return at


# -- responses ------------------------------------------------------------------------


class BuildPlanData(BaseModel):
    plan: Plan
    check: CheckResult
    resolved: bool = Field(
        description="True when the re-solve loop reached a plan the checker passes."
    )
    failure: str | None = Field(
        description="Why no valid plan was found, when none was."
    )


class CheckPlanData(BaseModel):
    check: CheckResult


class ReplanProgressData(BaseModel):
    """Never the walking minutes: with the fatigue cap they would give a guest's limit [C19]."""

    completed_stop_ids: list[str]
    skipped_stop_ids: list[str]
    current_location_node_id: str | None
    as_of: datetime
    source: Literal["sent", "stored", "assumed"] = Field(
        description=(
            "sent: the client reported completed or skipped stops. stored: they were "
            "stored for the plan. Either way an ended stop nobody reported is assumed "
            "done. assumed: no completed or skipped stop was reported (even if a "
            "location or walking was), so the party is taken to be on schedule."
        )
    )


class ReplanData(BaseModel):
    valid: bool = Field(
        description="True when the replanned day (completed stops + new horizon) passes the checker."
    )
    plan: Plan | None = Field(
        description="The candidate for the rest of the day, a new plan id; null when none was found."
    )
    diff: PlanDiff | None = Field(
        description="What changed against the plan that was sent."
    )
    check: CheckResult | None = Field(
        description="The whole day's check, counting the walking already done."
    )
    progress: ReplanProgressData = Field(
        description="The progress the replan started from: the one sent, the stored one, or an on-schedule assumption."
    )
    unmet_must_do: list[str]
    failure: str | None = Field(
        description="Why no valid replan was found, when none was."
    )


class AttractionUtility(BaseModel):
    attraction_id: str
    utility: float


class ScorePreferencesData(BaseModel):
    objective_version: str
    weights: dict[str, float]
    weight_provenance: dict[str, Literal["stated", "learned", "default"]] = Field(
        description="Where each weight came from: the guest's statements, learned behavior, or a default."
    )
    fairness: FairnessConfig
    top_attractions: list[AttractionUtility]
    per_guest_top: dict[str, list[AttractionUtility]]
    unmatched_affinities: list[str]
    model_version: str


class ForecastPoint(BaseModel):
    attraction_id: str
    at: datetime
    wait_minutes: float
    strategy: str
    data_source: str
    snapshot_id: str | None
    as_of: datetime


class ForecastGap(BaseModel):
    attraction_id: str
    at: datetime


class ForecastWaitsData(BaseModel):
    forecasts: list[ForecastPoint]
    no_reading: list[ForecastGap] = Field(
        description="Never invented: no strategy had a reading."
    )
    unknown_ids: list[str]


# -- tools ----------------------------------------------------------------------------


def _context_provenance(
    context: ContextStamp, strategy: str, version: str | None = None
) -> ToolProvenance:
    return ToolProvenance(
        source=PLANNER_SOURCE,
        snapshot_id=context.snapshot_id,
        retrieved_at=context.retrieved_at,
        age_seconds=context.age.total_seconds(),
        stale=context.stale,
        strategy=strategy,
        version=version,
    )


def _top(utilities: dict[str, float] | Any, n: int) -> list[AttractionUtility]:
    ranked = sorted(utilities.items(), key=lambda item: (-item[1], item[0]))[:n]
    return [AttractionUtility(attraction_id=a, utility=round(u, 6)) for a, u in ranked]


def planner_tools(ctx: ToolContext) -> list[ToolSpec]:
    queries = PlannerQueries(ctx.deps_factory)

    def build_plan(request: BuildPlanRequest) -> ToolResult[BuildPlanData]:
        candidate = queries.build_plan(
            session_id=request.session_id,
            constraints=request.constraints,
            profiles=request.profiles,
            guest_ids=request.guest_ids,
            now=request.now or ctx.clock(),
        )
        provenance = candidate.plan.provenance
        return ToolResult[BuildPlanData](
            data=BuildPlanData(
                plan=candidate.plan,
                check=candidate.check,
                resolved=candidate.resolved,
                failure=candidate.failure,
            ),
            provenance=_context_provenance(
                candidate.context,
                provenance.optimizer_strategy,
                provenance.objective_version,
            ),
        )

    def check_plan(request: CheckPlanRequest) -> ToolResult[CheckPlanData]:
        answer = queries.check_plan(
            session_id=request.session_id,
            plan=request.plan,
            constraints=request.constraints,
            guest_ids=request.guest_ids,
            now=request.now or ctx.clock(),
        )
        return ToolResult[CheckPlanData](
            data=CheckPlanData(check=answer.check),
            provenance=_context_provenance(answer.context, "constraint_checker"),
        )

    def replan(request: ReplanToolRequest) -> ToolResult[ReplanData]:
        candidate = queries.replan(
            session_id=request.session_id,
            plan=request.plan,
            constraints=request.constraints,
            profiles=request.profiles,
            guest_ids=request.guest_ids,
            now=request.now or ctx.clock(),
            execution_state=request.execution_state,
            events=request.events,
            rest_first=request.rest_first,
        )
        provenance = (candidate.plan or request.plan).provenance
        return ToolResult[ReplanData](
            data=ReplanData(
                valid=candidate.valid,
                plan=candidate.plan,
                diff=candidate.diff,
                check=candidate.check,
                progress=ReplanProgressData(**asdict(candidate.progress)),
                unmet_must_do=candidate.unmet_must_do,
                failure=candidate.failure,
            ),
            provenance=_context_provenance(
                candidate.context,
                f"replanner+{provenance.optimizer_strategy}",
                provenance.objective_version,
            ),
        )

    def score_preferences(
        request: ScorePreferencesRequest,
    ) -> ToolResult[ScorePreferencesData]:
        answer = queries.score_preferences(
            session_id=request.session_id,
            constraints=request.constraints,
            profiles=request.profiles,
            guest_ids=request.guest_ids,
            now=request.now or ctx.clock(),
        )
        objective, scores = answer.objective, answer.scores
        return ToolResult[ScorePreferencesData](
            data=ScorePreferencesData(
                objective_version=objective.objective_version,
                weights=dict(objective.weights),
                weight_provenance={
                    k: v.value for k, v in objective.weight_provenance.items()
                },
                fairness=objective.fairness,
                top_attractions=_top(scores.group, request.top),
                per_guest_top={
                    g: _top(u, request.top) for g, u in scores.per_guest.items()
                },
                unmatched_affinities=list(scores.unmatched_affinities),
                model_version=scores.model_version,
            ),
            provenance=_context_provenance(
                answer.context, "preference_scorer", scores.model_version
            ),
        )

    def forecast_waits(request: ForecastWaitsRequest) -> ToolResult[ForecastWaitsData]:
        now = request.now or ctx.clock()
        answer = queries.forecast_waits(
            request.attraction_ids, request.at or [now], now
        )
        strategies = sorted({f.strategy for f in answer.forecasts})
        return ToolResult[ForecastWaitsData](
            data=ForecastWaitsData(
                forecasts=[
                    ForecastPoint(
                        attraction_id=f.attraction_id,
                        at=f.at,
                        wait_minutes=f.wait_minutes,
                        strategy=f.strategy,
                        data_source=f.data_source.value,
                        snapshot_id=f.snapshot_id,
                        as_of=f.as_of,
                    )
                    for f in answer.forecasts
                ],
                no_reading=[
                    ForecastGap(attraction_id=a, at=t) for a, t in answer.no_reading
                ],
                unknown_ids=answer.unknown_ids,
            ),
            provenance=ToolProvenance(
                source=FORECAST_SOURCE, strategy="+".join(strategies) or None
            ),
        )

    def spec(
        name: str,
        description: str,
        request: type[BaseModel],
        data: type[BaseModel],
        handler: Callable[[Any], ToolResult[Any]],
    ) -> ToolSpec:
        return ToolSpec("planner", name, description, request, data, handler)

    return [
        spec(
            "build_plan",
            "Build a candidate day plan for a party (deterministic optimizer, re-solved against the "
            "11 rules) and check it. Never proposes or activates it.",
            BuildPlanRequest,
            BuildPlanData,
            build_plan,
        ),
        spec(
            "check_plan",
            "Check a plan against the 11 constraint rules; violations name the rule and the stop.",
            CheckPlanRequest,
            CheckPlanData,
            check_plan,
        ),
        spec(
            "replan",
            "Replan the rest of the day from a current plan, its progress and what happened "
            "(closures, reopenings): completed stops are kept, the new horizon is re-solved and "
            "the whole day is checked against the 11 rules. Events sent here override the "
            "snapshot's availability whatever their timestamp; their severity and "
            "requires_replan are ignored. Returns a candidate and its diff; "
            "never proposes or activates it.",
            ReplanToolRequest,
            ReplanData,
            replan,
        ),
        spec(
            "score_preferences",
            "How the party's preferences became weights (stated, learned or default) and which "
            "attractions they favor, for the party and per guest.",
            ScorePreferencesRequest,
            ScorePreferencesData,
            score_preferences,
        ),
        spec(
            "forecast_waits",
            "Expected standby waits at given arrival times, with the forecasting strategy that answered.",
            ForecastWaitsRequest,
            ForecastWaitsData,
            forecast_waits,
        ),
    ]
