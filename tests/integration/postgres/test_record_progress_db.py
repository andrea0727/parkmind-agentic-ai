"""On real tables: a reported stop lands in the stored progress and the behavior log.

The party's guests are registered under their thread-scoped id in the same
transaction, so ``behavior_entries.guest_id`` keeps its foreign key, like a decision.
"""

import threading
import time
from dataclasses import replace
from datetime import timedelta
from typing import Any

import factories
import psycopg
import pytest
from planning_support import factory_for, make_deps
from psycopg.rows import dict_row

from parkmind.core.contracts import (
    ApprovalStatus,
    BehaviorEventType,
    GuestRole,
    PlanExecutionState,
)
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


PLAN = factories.plan(
    stops=[
        factories.stop(node_id="a1", served_guests=[]),
        factories.stop(node_id="a2", served_guests=[]),
    ],
    per_guest_satisfaction={"g1": 0.9, "g2": 0.7},
)


def _deps_on(connection: psycopg.Connection) -> Any:
    return replace(
        make_deps(),
        plans=PostgresPlanRepository(connection),
        proposals=PostgresProposalRepository(connection),
        execution_states=PostgresExecutionStateRepository(connection),
        guests=PostgresGuestRepository(connection),
        behavior=PostgresBehaviorLogRepository(connection),
        transaction=lambda: connection.transaction(),
    )


@pytest.fixture
def deps(conn: psycopg.Connection):
    plans = PostgresPlanRepository(conn)
    proposals = PostgresProposalRepository(conn)
    plans.save("t1", PLAN)
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


def _report(deps, outcome, *, node_id="a1", at=NOW):
    return RecordProgressUseCase(factory_for(deps)).execute(
        "t1", "plan_1", node_id, outcome, at=at, guests=PARTY
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


def test_a_repeated_report_later_logs_once_and_never_moves_the_progress(conn, deps):
    _report(deps, StopOutcome.COMPLETED)
    again = _report(deps, StopOutcome.COMPLETED, at=NOW + timedelta(minutes=30))

    assert not again.changed
    state = PostgresExecutionStateRepository(conn).get("plan_1")
    assert state is not None
    assert (state.completed_stop_ids, state.as_of) == (["a1"], NOW)
    assert len(PostgresBehaviorLogRepository(conn).get("t1:g1").entries) == 1


def test_a_report_waits_for_a_concurrent_writer_instead_of_overwriting_it(
    conn, migrated_database_url
):
    """The lost update Andrea found in PR #99: two writers of the same plan's progress.

    ``writer`` holds the plan's lock, as a first report (or an event) would, and saves
    a1. Meanwhile a second report of a2 starts on its own connection: it must wait, then
    read a1 and add a2. Without the lock it reads the old state and one stop is lost.
    """
    plans = PostgresPlanRepository(conn)
    proposals = PostgresProposalRepository(conn)
    plans.save("t1", PLAN)
    proposals.save("t1", factories.proposal())
    proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)
    plans.activate("t1", "plan_1", at=NOW)

    writer = psycopg.connect(migrated_database_url, row_factory=dict_row)
    reporter = psycopg.connect(migrated_database_url, row_factory=dict_row)
    outcome: dict[str, Any] = {}

    def report_a2() -> None:
        try:
            outcome["result"] = _report(
                _deps_on(reporter), StopOutcome.COMPLETED, node_id="a2"
            )
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
            outcome["error"] = exc

    try:
        with writer.transaction():
            PostgresExecutionStateRepository(writer).lock("plan_1")
            thread = threading.Thread(target=report_a2)
            thread.start()
            time.sleep(0.5)
            assert thread.is_alive(), "the second report did not wait for the lock"
            PostgresExecutionStateRepository(writer).save(
                PlanExecutionState(
                    plan_id="plan_1", completed_stop_ids=["a1"], as_of=NOW
                )
            )
        thread.join(timeout=10)
    finally:
        writer.close()
        reporter.close()

    assert "error" not in outcome, outcome.get("error")
    state = PostgresExecutionStateRepository(conn).get("plan_1")
    assert state is not None and state.completed_stop_ids == ["a1", "a2"]
