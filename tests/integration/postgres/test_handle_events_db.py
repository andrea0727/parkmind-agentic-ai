"""P0-33 on real tables: idempotent recording, supersede and execution state in one transaction."""

from dataclasses import replace
from datetime import datetime, timedelta

import factories
import psycopg
import pytest
from planning_support import PlanningAttractionRepository, factory_for, make_deps

from parkmind.core.contracts import (
    PARK_TZ,
    ApprovalStatus,
    EventSource,
    EventThresholds,
    EventType,
    FairnessConfig,
    GroupObjective,
    HardConstraintSet,
    StopKind,
)
from parkmind.services.clients.postgres import (
    PostgresEventRepository,
    PostgresExecutionStateRepository,
    PostgresPlanRepository,
    PostgresProposalRepository,
)
from parkmind.services.planning.event_policy import UnconfirmedEventError
from parkmind.services.use_cases.handle_events import HandleEventsUseCase

NOW = datetime(2026, 9, 16, 10, 30, tzinfo=PARK_TZ)
THREAD = "thread-db"

PLAN = factories.plan(
    plan_id="plan_db",
    stops=[
        factories.stop(
            node_id="space",
            kind=StopKind.ATTRACTION,
            arrival_time=NOW,
            departure_time=NOW + timedelta(minutes=45),
            walking_minutes=10.0,
        )
    ],
)
OBJECTIVE = GroupObjective(
    objective_version="t",
    weights={},
    hard_constraints=HardConstraintSet(),
    fairness=FairnessConfig(lambda_fairness=0.3, min_satisfaction_floor=0.0),
    event_thresholds=EventThresholds(),
)


@pytest.fixture
def deps(conn: psycopg.Connection):
    plans = PostgresPlanRepository(conn)
    proposals = PostgresProposalRepository(conn)
    plans.save(THREAD, PLAN)
    plans.save(THREAD, factories.plan(plan_id="plan_candidate"))
    proposals.save(
        THREAD,
        factories.proposal(
            proposal_id="prop_pending", candidate_plan_id="plan_candidate"
        ),
    )
    return replace(
        make_deps(
            attractions=PlanningAttractionRepository(
                [factories.attraction(node_id="space")]
            )
        ),
        plans=plans,
        proposals=proposals,
        events=PostgresEventRepository(conn),
        execution_states=PostgresExecutionStateRepository(conn),
        transaction=lambda: conn.transaction(),
    )


def _handle(deps, candidates, *, confirmed=True):
    return HandleEventsUseCase(factory_for(deps)).execute(
        THREAD,
        candidates,
        plan=PLAN,
        constraints=factories.party_constraints(must_do=["space"]),
        objective=OBJECTIVE,
        accessibility_ref=[],
        confirmed=confirmed,
        now=NOW,
    )


def _down(source: EventSource):
    return factories.event(
        event_id=f"raw-{source.value}",
        type=EventType.ATTRACTION_DOWN,
        source=source,
        attraction_id="space",
        timestamp=NOW,
    )


def test_a_closure_from_both_sources_is_stored_once_and_supersedes_once(deps) -> None:
    first = _handle(deps, [_down(EventSource.MONITOR)])
    second = _handle(deps, [_down(EventSource.USER)])

    stored = deps.events.list_for_thread(THREAD)
    assert [e.event_id for e in stored] == ["ATTRACTION_DOWN:space:1"]
    assert stored[0].requires_replan is True and stored[0].severity is not None
    assert first.superseded == ["prop_pending"] and second.superseded == []
    assert (
        deps.proposals.get("prop_pending").approval_status is ApprovalStatus.SUPERSEDED
    )


def test_a_refused_event_rolls_the_whole_batch_back(deps) -> None:
    moved = factories.event(
        event_id="raw-move",
        type=EventType.PARTY_RELOCATED,
        source=EventSource.USER,
        attraction_id=None,
        location_node_id="frontierland",
        confidence=0.95,
        timestamp=NOW,
    )
    tired = factories.event(
        event_id="raw-tired",
        type=EventType.GUEST_FATIGUE,
        source=EventSource.USER,
        attraction_id=None,
        guest_id="g1",
        confidence=0.3,
        timestamp=NOW,
    )

    with pytest.raises(UnconfirmedEventError):
        _handle(deps, [moved, tired], confirmed=False)

    assert deps.events.list_for_thread(THREAD) == []
    assert deps.execution_states.get(PLAN.plan_id) is None
    assert [p.proposal_id for p in deps.proposals.list_pending(THREAD)] == [
        "prop_pending"
    ]


def test_a_relocation_is_stored_in_the_execution_state(deps) -> None:
    moved = factories.event(
        event_id="raw-move",
        type=EventType.PARTY_RELOCATED,
        source=EventSource.USER,
        attraction_id=None,
        location_node_id="frontierland",
        confidence=0.95,
        timestamp=NOW,
    )

    handled = _handle(deps, [moved])

    assert handled.superseded == []
    assert (
        deps.execution_states.get(PLAN.plan_id).current_location_node_id
        == "frontierland"
    )
