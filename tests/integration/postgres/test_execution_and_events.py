"""P0-12: execution state (section 33 [C13]) and events (section 33 [C10], section 43)."""

from datetime import timedelta

import factories
import psycopg
import pytest

from parkmind.core.contracts import EventSeverity, EventSource, EventType
from parkmind.services.clients.postgres.event_repository import PostgresEventRepository
from parkmind.services.clients.postgres.execution_state_repository import (
    PostgresExecutionStateRepository,
)
from parkmind.services.clients.postgres.plan_repository import PostgresPlanRepository
from parkmind.services.ports import NotFoundError

NOW = factories.NOW

# ------------------------------------------------------------------ execution state


def _with_plan(conn: psycopg.Connection) -> PostgresExecutionStateRepository:
    PostgresPlanRepository(conn).save("t1", factories.plan(plan_id="plan_1"))
    return PostgresExecutionStateRepository(conn)


def test_execution_state_round_trips(conn: psycopg.Connection) -> None:
    repo = _with_plan(conn)
    state = factories.execution_state()

    assert repo.save(state) is True

    assert repo.get("plan_1") == state
    assert repo.get("nope") is None


def test_a_newer_execution_state_replaces_the_stored_one(conn: psycopg.Connection) -> None:
    repo = _with_plan(conn)
    repo.save(factories.execution_state(completed_stop_ids=["a1"]))
    newer = factories.execution_state(
        completed_stop_ids=["a1", "a2"], as_of=NOW + timedelta(minutes=30)
    )

    assert repo.save(newer) is True

    assert repo.get("plan_1") == newer


def test_an_older_execution_state_cannot_roll_progress_back(conn: psycopg.Connection) -> None:
    repo = _with_plan(conn)
    newer = factories.execution_state(
        completed_stop_ids=["a1", "a2"], as_of=NOW + timedelta(minutes=30)
    )
    repo.save(newer)

    accepted = repo.save(factories.execution_state(completed_stop_ids=["a1"], as_of=NOW))

    assert accepted is False
    assert repo.get("plan_1") == newer


def test_execution_state_for_an_unsaved_plan_is_refused(conn: psycopg.Connection) -> None:
    with pytest.raises(NotFoundError):
        PostgresExecutionStateRepository(conn).save(factories.execution_state(plan_id="ghost"))


# ----------------------------------------------------------------------------- events


def test_event_round_trips_with_the_policy_fields_it_was_given(conn: psycopg.Connection) -> None:
    repo = PostgresEventRepository(conn)
    event = factories.event(severity=EventSeverity.HIGH, requires_replan=True)

    assert repo.record("t1", event) is True

    assert repo.list_for_thread("t1") == [event]


def test_recording_the_same_event_twice_is_idempotent(conn: psycopg.Connection) -> None:
    repo = PostgresEventRepository(conn)

    assert repo.record("t1", factories.event()) is True
    assert repo.record("t1", factories.event()) is False

    assert len(repo.list_for_thread("t1")) == 1


def test_a_park_wide_event_is_recorded_once_per_thread(conn: psycopg.Connection) -> None:
    repo = PostgresEventRepository(conn)

    assert repo.record("t1", factories.event(event_id="ev_1")) is True
    assert repo.record("t2", factories.event(event_id="ev_1")) is True

    assert len(repo.list_for_thread("t1")) == 1
    assert len(repo.list_for_thread("t2")) == 1


def test_events_are_ordered_by_time_and_keep_aware_timestamps(conn: psycopg.Connection) -> None:
    repo = PostgresEventRepository(conn)
    late = factories.event(
        event_id="ev_late",
        type=EventType.GUEST_FATIGUE,
        source=EventSource.USER,
        attraction_id=None,
        guest_id="g1",
        timestamp=NOW + timedelta(hours=1),
    )
    early = factories.event(event_id="ev_early")

    repo.record("t1", late)
    repo.record("t1", early)

    stored = repo.list_for_thread("t1")
    assert [e.event_id for e in stored] == ["ev_early", "ev_late"]
    assert all(e.timestamp.tzinfo is not None for e in stored)


def test_since_is_inclusive_of_the_boundary_event(conn: psycopg.Connection) -> None:
    repo = PostgresEventRepository(conn)
    repo.record("t1", factories.event(event_id="ev_1", timestamp=NOW))
    repo.record("t1", factories.event(event_id="ev_2", timestamp=NOW + timedelta(minutes=10)))
    repo.record("t1", factories.event(event_id="ev_3", timestamp=NOW + timedelta(minutes=20)))

    ids = [e.event_id for e in repo.list_for_thread("t1", since=NOW + timedelta(minutes=10))]

    assert ids == ["ev_2", "ev_3"]


def test_events_of_other_threads_are_not_listed(conn: psycopg.Connection) -> None:
    repo = PostgresEventRepository(conn)
    repo.record("t2", factories.event())

    assert repo.list_for_thread("t1") == []
