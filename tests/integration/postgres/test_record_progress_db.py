"""On real tables: a reported stop lands in the stored progress and the behavior log.

The party's guests are registered under their thread-scoped id in the same
transaction, so ``behavior_entries.guest_id`` keeps its foreign key, like a decision.
"""

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
from parkmind.services.use_cases.resolve_proposal import ResolveProposalUseCase

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


def _activate_plan_1(conn: psycopg.Connection) -> None:
    plans = PostgresPlanRepository(conn)
    proposals = PostgresProposalRepository(conn)
    plans.save("t1", PLAN)
    proposals.save("t1", factories.proposal())
    proposals.resolve("prop_1", ApprovalStatus.APPROVED, at=NOW)
    plans.activate("t1", "plan_1", at=NOW)
    conn.commit()


@contextmanager
def _writer_holding_the_lock(url: str, plan_id: str) -> Iterator[psycopg.Connection]:
    """A second connection in an open transaction that holds the plan's lock."""
    writer = psycopg.connect(url, row_factory=dict_row)
    try:
        with writer.transaction():
            PostgresExecutionStateRepository(writer).lock(plan_id)
            yield writer
    finally:
        writer.close()


def _in_thread(action: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = action()
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertions
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _wait_until_a_session_waits_for_a_lock(url: str, *, timeout: float = 10.0) -> None:
    """Block until Postgres reports a session waiting on a lock: the second writer is
    really queued behind the first, not just slow."""
    deadline = time.monotonic() + timeout
    with psycopg.connect(url, autocommit=True) as admin:
        while time.monotonic() < deadline:
            waiting = admin.execute(
                "SELECT 1 FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ).fetchone()
            if waiting is not None:
                return
            time.sleep(0.02)
    pytest.fail("no session ever waited for the plan's lock")


def test_a_report_waits_for_a_concurrent_writer_instead_of_overwriting_it(
    conn, migrated_database_url
):
    """Two writers of the same plan's progress: the one holding the lock saves a1 while
    a report of a2 starts on its own connection. The report must wait, then read a1 and
    add a2. Without the lock it reads the old state and one stop is lost."""
    _activate_plan_1(conn)
    reporter = psycopg.connect(
        migrated_database_url, row_factory=dict_row, autocommit=True
    )
    try:
        with _writer_holding_the_lock(migrated_database_url, "plan_1") as writer:
            thread, outcome = _in_thread(
                lambda: _report(_deps_on(reporter), StopOutcome.COMPLETED, node_id="a2")
            )
            _wait_until_a_session_waits_for_a_lock(migrated_database_url)
            assert thread.is_alive()
            PostgresExecutionStateRepository(writer).save(
                PlanExecutionState(
                    plan_id="plan_1", completed_stop_ids=["a1"], as_of=NOW
                )
            )
        thread.join(timeout=10)
    finally:
        reporter.close()

    assert "error" not in outcome, outcome.get("error")
    state = PostgresExecutionStateRepository(conn).get("plan_1")
    assert state is not None and state.completed_stop_ids == ["a1", "a2"]


def test_a_report_that_waited_sees_the_plan_that_replaced_its_own(
    conn, migrated_database_url
):
    """The plan is checked to be the active one after the lock, not before: while the
    report waited, an approval made plan_2 the active plan, and the report is refused
    instead of writing progress to a plan nobody walks any more."""
    _activate_plan_1(conn)
    plans = PostgresPlanRepository(conn)
    proposals = PostgresProposalRepository(conn)
    plans.save("t1", factories.plan(plan_id="plan_2", stops=PLAN.stops))
    proposals.save(
        "t1",
        factories.proposal(
            proposal_id="prop_2", base_plan_id="plan_1", candidate_plan_id="plan_2"
        ),
    )
    proposals.resolve("prop_2", ApprovalStatus.APPROVED, at=NOW)
    conn.commit()
    reporter = psycopg.connect(
        migrated_database_url, row_factory=dict_row, autocommit=True
    )
    try:
        with _writer_holding_the_lock(migrated_database_url, "plan_1") as writer:
            thread, outcome = _in_thread(
                lambda: _report(_deps_on(reporter), StopOutcome.COMPLETED, node_id="a2")
            )
            _wait_until_a_session_waits_for_a_lock(migrated_database_url)
            PostgresPlanRepository(writer).activate("t1", "plan_2", at=NOW)
        thread.join(timeout=10)
    finally:
        reporter.close()

    error = outcome.get("error")
    assert isinstance(error, InvalidProgressError), outcome
    assert error.problem is ProgressProblem.NOT_THE_ACTIVE_PLAN
    assert PostgresExecutionStateRepository(conn).get("plan_1") is None
    assert PostgresBehaviorLogRepository(conn).get("t1:g1").entries == []


def test_approving_a_replan_waits_for_a_report_in_flight_and_carries_it_over(
    conn, migrated_database_url
):
    """The approval copies the base plan's progress to the new plan: a writer of that
    progress holding the lock finishes first, so the copy has its stop."""
    _activate_plan_1(conn)
    plans = PostgresPlanRepository(conn)
    proposals = PostgresProposalRepository(conn)
    plans.save("t1", factories.plan(plan_id="plan_2", stops=PLAN.stops))
    proposals.save(
        "t1",
        factories.proposal(
            proposal_id="prop_2", base_plan_id="plan_1", candidate_plan_id="plan_2"
        ),
    )
    conn.commit()
    approver = psycopg.connect(
        migrated_database_url, row_factory=dict_row, autocommit=True
    )
    try:
        with _writer_holding_the_lock(migrated_database_url, "plan_1") as writer:
            thread, outcome = _in_thread(
                lambda: ResolveProposalUseCase(
                    factory_for(_deps_on(approver))
                ).execute(
                    "t1", "prop_2", ApprovalStatus.APPROVED, at=NOW, guests=PARTY
                )
            )
            _wait_until_a_session_waits_for_a_lock(migrated_database_url)
            assert thread.is_alive()
            PostgresExecutionStateRepository(writer).save(
                PlanExecutionState(
                    plan_id="plan_1", completed_stop_ids=["a1", "a2"], as_of=NOW
                )
            )
        thread.join(timeout=10)
    finally:
        approver.close()

    assert "error" not in outcome, outcome.get("error")
    carried = PostgresExecutionStateRepository(conn).get("plan_2")
    assert carried is not None and carried.completed_stop_ids == ["a1", "a2"]
    active = PostgresPlanRepository(conn).get_active("t1")
    assert active is not None and active.plan_id == "plan_2"


def test_the_lock_does_not_block_rows_that_only_reference_the_plan(
    conn, migrated_database_url
):
    """Saving progress or activating a plan takes a key-share lock on the plan row
    through its foreign key. The progress lock must not conflict with it (FOR NO KEY
    UPDATE, not FOR UPDATE), or every such insert would queue behind a report."""
    _activate_plan_1(conn)
    other = psycopg.connect(
        migrated_database_url, row_factory=dict_row, autocommit=True
    )
    try:
        with _writer_holding_the_lock(migrated_database_url, "plan_1"):
            thread, outcome = _in_thread(
                lambda: PostgresExecutionStateRepository(other).save(
                    PlanExecutionState(plan_id="plan_1", as_of=NOW)
                )
            )
            thread.join(timeout=5)
            assert not thread.is_alive(), "the insert waited for the plan's lock"
    finally:
        other.close()

    assert "error" not in outcome, outcome.get("error")
