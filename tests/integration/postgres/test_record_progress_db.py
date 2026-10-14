"""P1-01 on real tables: a reported stop lands in the stored progress and the behavior log.

The party's guests are registered under their thread-scoped id in the same
transaction, so ``behavior_entries.guest_id`` keeps its foreign key, like a decision.
"""

from dataclasses import replace

import factories
import psycopg
import pytest
from planning_support import factory_for, make_deps

from parkmind.core.contracts import ApprovalStatus, BehaviorEventType, GuestRole
from parkmind.services.clients.postgres import (
    PostgresBehaviorLogRepository,
    PostgresExecutionStateRepository,
    PostgresGuestRepository,
    PostgresPlanRepository,
    PostgresProposalRepository,
)
from parkmind.services.use_cases.record_progress import (
    InvalidProgressError,
    ProgressProblem,
    RecordProgressUseCase,
    StopOutcome,
)

NOW = factories.NOW
PARTY = [
    factories.guest(guest_id="g1"),
    factories.guest(guest_id="g2", role=GuestRole.CHILD, height_cm=110),
]


@pytest.fixture
def deps(conn: psycopg.Connection):
    plans = PostgresPlanRepository(conn)
    proposals = PostgresProposalRepository(conn)
    plans.save(
        "t1", factories.plan(stops=[factories.stop(node_id="a1", served_guests=[])])
    )
    proposals.save("t1", factories.proposal())
    proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)
    plans.activate("t1", "plan_1", at=NOW)
    return replace(
        make_deps(),
        plans=plans,
        proposals=proposals,
        execution_states=PostgresExecutionStateRepository(conn),
        guests=PostgresGuestRepository(conn),
        behavior=PostgresBehaviorLogRepository(conn),
        transaction=lambda: conn.transaction(),
    )


def _report(deps, outcome):
    return RecordProgressUseCase(factory_for(deps)).execute(
        "t1", "plan_1", "a1", outcome, at=NOW, guests=PARTY
    )


def test_a_completed_stop_is_stored_and_logged_per_guest(conn, deps) -> None:
    _report(deps, StopOutcome.COMPLETED)
    _report(deps, StopOutcome.COMPLETED)

    state = PostgresExecutionStateRepository(conn).get("plan_1")
    assert state is not None and state.completed_stop_ids == ["a1"]
    behavior = PostgresBehaviorLogRepository(conn)
    for guest_id in ("t1:g1", "t1:g2"):
        (entry,) = behavior.get(guest_id).entries
        assert entry.event_type is BehaviorEventType.ATTRACTION_COMPLETED
        assert entry.attraction_ids == ["a1"]
    child = PostgresGuestRepository(conn).get("t1:g2")
    assert child is not None and child.height_cm is None


def test_a_refused_report_leaves_the_tables_unchanged(conn, deps) -> None:
    _report(deps, StopOutcome.SKIPPED)

    with pytest.raises(InvalidProgressError) as raised:
        _report(deps, StopOutcome.COMPLETED)

    assert raised.value.problem is ProgressProblem.OTHER_OUTCOME_RECORDED
    state = PostgresExecutionStateRepository(conn).get("plan_1")
    assert state is not None
    assert (state.completed_stop_ids, state.skipped_stop_ids) == ([], ["a1"])
    assert len(PostgresBehaviorLogRepository(conn).get("t1:g1").entries) == 1
