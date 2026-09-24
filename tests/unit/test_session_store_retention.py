"""SessionStore retention without a database (Architecture section 12, C19).

A recording fake connection stands in for Postgres, so these tests can state
precisely which statements a call did -- or did not -- issue.
"""

from contextlib import contextmanager, nullcontext
from typing import Any

import factories
import pytest

from parkmind.services.clients.postgres.session_store import (
    PostgresSessionStore,
    SessionMemory,
)
from parkmind.services.ports import ConsentRequiredError


class RecordingCursor:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    def execute(self, query: str, params: Any = None) -> None:
        self._statements.append(" ".join(query.split()))

    def fetchone(self) -> None:
        return None


class RecordingConnection:
    """Answers every query with 'no rows' and remembers the SQL it was given."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def transaction(self) -> Any:
        return nullcontext()

    @contextmanager
    def cursor(self, **_: Any) -> Any:
        yield RecordingCursor(self.statements)


def _session_only(guest_id: str = "g1", **overrides: Any) -> Any:
    return factories.accessibility(guest_id=guest_id, retention_policy="session_only", **overrides)


def test_session_only_never_touches_the_database() -> None:
    conn = RecordingConnection()
    store = PostgresSessionStore(conn, SessionMemory())  # type: ignore[arg-type]
    record = _session_only()

    store.put("s1", record)
    assert store.get("s1", "g1") == record
    store.end_session("s1")

    assert conn.statements == []


def test_a_session_only_record_is_gone_after_the_session_ends() -> None:
    conn = RecordingConnection()
    store = PostgresSessionStore(conn, SessionMemory())  # type: ignore[arg-type]
    store.put("s1", _session_only())

    store.end_session("s1")

    assert store.get("s1", "g1") is None
    # The miss looked at persisted storage only; nothing was ever written.
    assert all(s.startswith("SELECT") for s in conn.statements)


def test_session_records_are_isolated_per_session_and_per_guest() -> None:
    store = PostgresSessionStore(RecordingConnection(), SessionMemory())  # type: ignore[arg-type]
    mine = _session_only(daily_walking_limit_minutes=60)
    yours = _session_only(daily_walking_limit_minutes=120)
    other_guest = _session_only("g2")

    store.put("s1", mine)
    store.put("s2", yours)
    store.put("s1", other_guest)
    store.end_session("s1")

    assert store.get("s2", "g1") == yours
    assert store.get("s2", "g2") is None
    assert store.get("s1", "g1") is None


def test_a_session_record_survives_across_requests_sharing_the_process_memory() -> None:
    """Regression: the store used to own the memory, so a request-per-connection
    app (a new store each request) lost the record -- and the planner would have
    treated the guest as unrestricted."""
    memory = SessionMemory()  # one per process
    first_request, second_request = RecordingConnection(), RecordingConnection()
    record = _session_only()

    PostgresSessionStore(first_request, memory).put("s1", record)  # type: ignore[arg-type]
    seen = PostgresSessionStore(second_request, memory).get("s1", "g1")  # type: ignore[arg-type]

    assert seen == record
    assert first_request.statements == [] and second_request.statements == []

    PostgresSessionStore(RecordingConnection(), memory).end_session("s1")  # type: ignore[arg-type]
    assert memory.get("s1", "g1") is None


def test_ending_an_unknown_session_is_harmless() -> None:
    PostgresSessionStore(RecordingConnection(), SessionMemory()).end_session("never-started")  # type: ignore[arg-type]


def test_persisting_without_consent_is_refused_before_any_write() -> None:
    conn = RecordingConnection()
    store = PostgresSessionStore(conn, SessionMemory())  # type: ignore[arg-type]
    # No flags, so the contract itself allows consent=False here.
    no_consent = factories.accessibility(
        daily_walking_limit_minutes=None,
        mobility_requirements=[],
        ride_restrictions=[],
        consent=False,
        retention_policy="persisted",
    )

    with pytest.raises(ConsentRequiredError):
        store.put("s1", no_consent)

    assert conn.statements == []


def test_persisted_records_are_written_to_the_accessibility_table() -> None:
    conn = RecordingConnection()
    store = PostgresSessionStore(conn, SessionMemory())  # type: ignore[arg-type]

    store.put("s1", factories.accessibility(retention_policy="persisted"))

    assert len(conn.statements) == 1
    assert conn.statements[0].startswith("INSERT INTO accessibility_requirements")
