"""
Replanner walking-budget and rest-first behavior (P0-23 review follow-up, P0-34).

Real Replanner + PlannerResolveLoop + GreedyInsertionOptimizer + ConstraintChecker:
a replanned day is valid only if the WHOLE day (completed stops + horizon) passes the
same rules as an initial plan, so what was already walked counts against the limits.
"""

from datetime import datetime, timedelta, timezone

import pytest

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    AttractionCategory,
    AttractionStatus,
    Guest,
    LiveContext,
    Park,
    PartyConstraints,
    Plan,
    PlanExecutionState,
    Provenance,
    RuleId,
    Stop,
    StopKind,
)
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.replanner import Replanner, ReplanResult
from parkmind.services.planning.resolve_loop import PlannerResolveLoop

TZ = timezone.utc
START = datetime(2026, 10, 11, 9, 0, tzinfo=TZ)
AS_OF = START + timedelta(minutes=40)
NODES = ["attr_a", "attr_b", "attr_c", "attr_d", "attr_e"]


class FlatGraph:
    """Every hop between distinct nodes takes 5 minutes."""

    def walk_minutes(self, origin: str, destination: str) -> float:
        return 0.0 if origin == destination else 5.0


@pytest.fixture
def replanner() -> Replanner:
    graph = FlatGraph()
    loop = PlannerResolveLoop(GreedyInsertionOptimizer(park_graph=graph), ConstraintChecker())  # type: ignore[arg-type]
    return Replanner(loop, graph)  # type: ignore[arg-type]


@pytest.fixture
def park() -> Park:
    return Park(
        park_id="p", name="P", opening_time=START, closing_time=START + timedelta(hours=12)
    )


@pytest.fixture
def catalog() -> list[Attraction]:
    return [
        Attraction(
            node_id=n, name=n.upper(), typical_wait_minutes=10, category=AttractionCategory.FAMILY
        )
        for n in NODES
    ]


@pytest.fixture
def context() -> LiveContext:
    return LiveContext(
        snapshot_id="s2",
        retrieved_at=AS_OF,
        statuses={n: AttractionStatus.OPERATING for n in NODES},
        waits={},
        showtimes={},
        weather=[],
        accessibility_results=[],
        coverage={
            "required_attractions_covered": True,
            "required_shows_covered": True,
            "weather_covered": True,
            "accessibility_checks_complete": True,
        },
    )


def _constraints(park: Park, must_do: list[str] | None = None) -> PartyConstraints:
    return PartyConstraints(
        party_size=1,
        guests=[Guest(guest_id="g1", role="adult")],
        must_do=must_do or [],
        departure_time=park.closing_time,
        constraints_version=1,
    )


def _plan_with_completed(walked: float) -> Plan:
    stop = Stop(
        node_id="attr_a",
        kind=StopKind.ATTRACTION,
        arrival_time=START,
        departure_time=START + timedelta(minutes=30),
        expected_wait_minutes=5,
        walking_minutes=walked,
        utility=10,
        served_guests=["g1"],
    )
    return Plan(
        plan_id="plan_old",
        version=1,
        stops=[stop],
        total_wait_minutes=5,
        total_walking_minutes=walked,
        objective_value=10,
        per_guest_satisfaction={"g1": 1.0},
        provenance=Provenance(
            snapshot_id="s1",
            retrieved_at=START,
            forecast_strategy="none",
            optimizer_strategy="greedy_insertion",
            constraints_version=1,
            objective_version="auto",
            preference_model_version="auto",
        ),
    )


def _state(**kwargs: dict[str, float]) -> PlanExecutionState:
    return PlanExecutionState(
        plan_id="plan_old",
        as_of=AS_OF,
        completed_stop_ids=["attr_a"],
        current_location_node_id="attr_a",
        **kwargs,
    )


def _replan(
    replanner: Replanner,
    park: Park,
    catalog: list[Attraction],
    context: LiveContext,
    *,
    walked: float = 5.9,
    daily_limit: int | None = None,
    must_do: list[str] | None = None,
    state: PlanExecutionState | None = None,
    rest_first: bool = False,
    party_budget: int | None = None,
) -> tuple[ReplanResult, list[AccessibilityRequirements], PartyConstraints, Plan]:
    reqs = (
        [AccessibilityRequirements(guest_id="g1", daily_walking_limit_minutes=daily_limit, consent=True)]
        if daily_limit is not None
        else []
    )
    constraints = _constraints(park, must_do)
    if party_budget is not None:
        constraints = constraints.model_copy(update={"party_walking_budget_minutes": party_budget})
    current = _plan_with_completed(walked)
    result = replanner.replan(
        current_plan=current,
        execution_state=state or _state(),
        context=context,
        constraints=constraints,
        utilities={n: 10.0 for n in NODES},
        park=park,
        catalog=catalog,
        accessibility_reqs=reqs,
        rest_first=rest_first,
    )
    return result, reqs, constraints, current


def _full_day_check(
    result: ReplanResult,
    reqs: list[AccessibilityRequirements],
    constraints: PartyConstraints,
    park: Park,
    catalog: list[Attraction],
    context: LiveContext,
    state: PlanExecutionState,
) -> bool:
    assert result.plan is not None
    return ConstraintChecker().check_replan(
        result.plan, constraints, reqs, {a.node_id: a for a in catalog}, park, context, AS_OF, state
    ).valid


def test_daily_limit_counts_what_was_already_walked(replanner, park, catalog, context) -> None:
    # 5.9 walked + 5 per stop: limit 20 leaves room for two stops (15.9), not three (20.9).
    result, reqs, constraints, _ = _replan(replanner, park, catalog, context, daily_limit=20)

    assert result.valid is True
    assert result.plan is not None
    assert result.plan.total_walking_minutes <= 20
    horizon = [s for s in result.plan.stops if s.node_id != "attr_a"]
    assert len(horizon) == 2
    assert _full_day_check(result, reqs, constraints, park, catalog, context, _state())


def test_horizon_alone_under_limit_but_full_day_over_never_returns_valid(
    replanner, park, catalog, context
) -> None:
    # The reviewer's case: the horizon fits the limit by itself, the whole day does not.
    # With every remaining attraction a must-do the replan cannot satisfy both: it must
    # fail closed rather than return valid=True for a day the checker rejects.
    result, *_ = _replan(
        replanner,
        park,
        catalog,
        context,
        daily_limit=20,
        must_do=["attr_b", "attr_c", "attr_d"],
    )

    assert result.valid is False
    assert result.plan is None
    assert result.fatal_error


def test_party_walking_budget_counts_what_was_already_walked(
    replanner, park, catalog, context
) -> None:
    result, *_ = _replan(replanner, park, catalog, context, party_budget=16)

    assert result.valid is True
    assert result.plan is not None
    assert result.plan.total_walking_minutes <= 16


def test_consumed_walking_in_state_counts_against_daily_limit(
    replanner, park, catalog, context
) -> None:
    # The state says g1 actually walked 12 minutes, more than the plan recorded (5.9).
    state = _state(walking_minutes_consumed={"g1": 12.0})
    result, *_ = _replan(replanner, park, catalog, context, daily_limit=20, state=state)

    assert result.valid is True
    assert result.plan is not None
    horizon = [s for s in result.plan.stops if s.node_id != "attr_a"]
    assert len(horizon) == 1  # 12 + 5 = 17 <= 20, 12 + 10 = 22 > 20


@pytest.mark.parametrize("cap", [5.0, 15.0, 25.0, 40.0])
def test_tight_remaining_cap_yields_a_shorter_valid_plan(
    replanner, park, catalog, context, cap
) -> None:
    state = _state(remaining_walking_cap_minutes={"g1": cap})
    result, *_ = _replan(replanner, park, catalog, context, daily_limit=480, state=state)

    assert result.valid is True, result.fatal_error
    assert result.plan is not None
    horizon_walking = sum(s.walking_minutes for s in result.plan.stops if s.node_id != "attr_a")
    assert horizon_walking <= cap


def test_rest_first_opens_the_horizon_with_a_rest_and_passes_the_checker(
    replanner, park, catalog, context
) -> None:
    state = _state(remaining_walking_cap_minutes={"g1": 40.0})
    result, reqs, constraints, _ = _replan(
        replanner, park, catalog, context, daily_limit=480, state=state, rest_first=True
    )

    assert result.valid is True, result.fatal_error
    assert result.plan is not None
    first_new = result.plan.stops[1]
    assert first_new.kind is StopKind.REST
    assert first_new.node_id == "attr_a"  # rests where the party stands
    assert first_new.arrival_time >= AS_OF
    assert result.plan.stops[0].node_id == "attr_a"  # completed stop preserved
    assert _full_day_check(result, reqs, constraints, park, catalog, context, state)


def test_rest_first_that_cannot_fit_fails_closed(replanner, park, catalog, context) -> None:
    late = AS_OF.replace(hour=20, minute=55)
    state = PlanExecutionState(
        plan_id="plan_old", as_of=late, completed_stop_ids=["attr_a"], current_location_node_id="attr_a"
    )
    result, *_ = _replan(replanner, park, catalog, context, state=state, rest_first=True)

    assert result.valid is False
    assert result.plan is None


def test_no_rest_first_means_no_forced_rest(replanner, park, catalog, context) -> None:
    result, *_ = _replan(replanner, park, catalog, context, daily_limit=480)

    assert result.plan is not None
    assert result.plan.stops[1].kind is StopKind.ATTRACTION


def _check_replan(
    plan: Plan,
    park: Park,
    catalog: list[Attraction],
    context: LiveContext,
    state: PlanExecutionState,
    *,
    daily_limit: int | None = None,
) -> tuple[bool, str]:
    reqs = (
        [AccessibilityRequirements(guest_id="g1", daily_walking_limit_minutes=daily_limit, consent=True)]
        if daily_limit is not None
        else []
    )
    result = ConstraintChecker().check_replan(
        plan, _constraints(park), reqs, {a.node_id: a for a in catalog}, park, context, AS_OF, state
    )
    return result.valid, "; ".join(v.message for v in result.violations)


def test_check_replan_rejects_a_horizon_over_the_remaining_fatigue_cap(
    park, catalog, context
) -> None:
    plan = _plan_with_completed(5.9)
    horizon = plan.stops[0].model_copy(
        update={"node_id": "attr_b", "arrival_time": AS_OF, "departure_time": AS_OF + timedelta(minutes=20), "walking_minutes": 12.0}
    )
    plan = plan.model_copy(update={"stops": [*plan.stops, horizon], "total_walking_minutes": 17.9})

    ok, why = _check_replan(
        plan, park, catalog, context, _state(remaining_walking_cap_minutes={"g1": 10.0}), daily_limit=480
    )

    assert not ok
    assert "remaining fatigue-adjusted cap" in why


def test_check_replan_does_not_charge_the_cap_for_what_was_already_walked(
    park, catalog, context
) -> None:
    # The completed stop walked 5.9: only the horizon counts against the cap.
    plan = _plan_with_completed(5.9)
    horizon = plan.stops[0].model_copy(
        update={"node_id": "attr_b", "arrival_time": AS_OF, "departure_time": AS_OF + timedelta(minutes=20), "walking_minutes": 5.0}
    )
    plan = plan.model_copy(update={"stops": [*plan.stops, horizon], "total_walking_minutes": 10.9})

    ok, why = _check_replan(
        plan, park, catalog, context, _state(remaining_walking_cap_minutes={"g1": 6.0}), daily_limit=480
    )

    assert ok, why


def _plan_with_horizon(walked: float, horizon_walking: float) -> Plan:
    plan = _plan_with_completed(walked)
    horizon = plan.stops[0].model_copy(
        update={
            "node_id": "attr_b",
            "arrival_time": AS_OF,
            "departure_time": AS_OF + timedelta(minutes=20),
            "walking_minutes": horizon_walking,
        }
    )
    return plan.model_copy(
        update={"stops": [*plan.stops, horizon], "total_walking_minutes": walked + horizon_walking}
    )


def test_check_replan_counts_the_walking_recorded_for_a_guest(park, catalog, context) -> None:
    # The plan schedules 5.9 + 10 = 15.9 <= 20, but the guest is recorded at 12 walked: 12 + 10 > 20.
    plan = _plan_with_horizon(5.9, 10.0)

    ok, why = _check_replan(
        plan, park, catalog, context, _state(walking_minutes_consumed={"g1": 12.0}), daily_limit=20
    )

    assert not ok
    assert "g1 walking total 22.0 minutes exceeds" in why


def test_check_replan_accepts_recorded_walking_that_still_fits(park, catalog, context) -> None:
    plan = _plan_with_horizon(5.9, 5.0)

    ok, why = _check_replan(
        plan, park, catalog, context, _state(walking_minutes_consumed={"g1": 12.0}), daily_limit=20
    )

    assert ok, why


def test_check_replan_reports_the_daily_limit_once_when_the_plan_already_exceeds_it(
    park, catalog, context
) -> None:
    plan = _plan_with_horizon(5.9, 20.0)

    result = ConstraintChecker().check_replan(
        plan,
        _constraints(park),
        [AccessibilityRequirements(guest_id="g1", daily_walking_limit_minutes=20, consent=True)],
        {a.node_id: a for a in catalog},
        park,
        context,
        AS_OF,
        _state(walking_minutes_consumed={"g1": 3.0}),
    )

    limit = [v for v in result.violations if "accessibility limit" in v.message]
    assert len(limit) == 1


def test_check_replan_does_not_hold_a_closure_against_a_stop_already_completed(
    park, catalog, context
) -> None:
    closed = context.model_copy(
        update={"statuses": {**context.statuses, "attr_a": AttractionStatus.DOWN}}
    )
    plan = _plan_with_completed(5.9)

    done = _check_replan(plan, park, catalog, closed, _state())
    still_ahead = _check_replan(
        plan,
        park,
        catalog,
        closed,
        PlanExecutionState(plan_id="plan_old", as_of=AS_OF, current_location_node_id="attr_a"),
    )

    assert done[0], done[1]
    assert not still_ahead[0]


def test_check_offsets_the_walking_limits_by_what_was_already_walked(
    park, catalog, context
) -> None:
    full = _plan_with_horizon(0.0, 15.0)
    horizon_only = full.model_copy(update={"stops": full.stops[1:], "total_walking_minutes": 15.0})
    reqs = [AccessibilityRequirements(guest_id="g1", daily_walking_limit_minutes=20, consent=True)]
    args = (_constraints(park), reqs, {a.node_id: a for a in catalog}, park, context, AS_OF)

    assert ConstraintChecker().check(horizon_only, *args).valid
    offset = ConstraintChecker().check(horizon_only, *args, walking_done_minutes=10.0)
    assert not offset.valid
    assert {v.rule for v in offset.violations} == {RuleId.ACCESSIBILITY}
