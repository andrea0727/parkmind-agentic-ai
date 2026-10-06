"""Accessibility intake: consent and retention [C15, C19]."""

from contextlib import contextmanager, nullcontext
from typing import Any

import factories
import pytest
from elicit_support import make_intake

from parkmind.core.contracts import MobilityRequirement
from parkmind.services.clients.postgres.session_store import (
    PostgresSessionStore,
    SessionMemory,
)
from parkmind.services.ports import ConsentRequiredError
from parkmind.services.use_cases.accessibility_intake import (
    DEFAULT_RETENTION,
    StagedAccessibility,
)

LIMITED = StagedAccessibility(
    guest_id="g2", mobility_requirements=(MobilityRequirement.LIMITED_WALKING,)
)


def test_default_retention_is_session_only() -> None:
    assert DEFAULT_RETENTION == "session_only"
    intake, store = make_intake()
    intake.stage("s1", LIMITED)

    assert intake.commit("s1", consent=True) == ["g2"]

    record = store.get("s1", "g2")
    assert record is not None
    assert record.retention_policy == "session_only"
    assert record.consent is True
    assert record.mobility_requirements == [MobilityRequirement.LIMITED_WALKING]


def test_commit_without_consent_is_refused_and_writes_nothing() -> None:
    intake, store = make_intake()
    intake.stage("s1", LIMITED)

    with pytest.raises(ConsentRequiredError):
        intake.commit("s1", consent=False)

    assert store.puts == 0
    assert intake.pending_guest_ids("s1") == ["g2"]  # still waiting for consent


def test_persisted_retention_is_carried_through() -> None:
    intake, store = make_intake()
    intake.stage("s1", LIMITED)

    intake.commit("s1", consent=True, retention_policy="persisted")

    record = store.get("s1", "g2")
    assert record is not None and record.retention_policy == "persisted"


def test_commit_clears_staging() -> None:
    intake, _ = make_intake()
    intake.stage("s1", LIMITED)

    intake.commit("s1", consent=True)

    assert intake.pending_guest_ids("s1") == []
    assert intake.commit("s1", consent=True) == []


def test_flagless_entries_are_not_staged() -> None:
    intake, _ = make_intake()

    intake.stage("s1", StagedAccessibility(guest_id="g1"))

    assert intake.pending_guest_ids("s1") == []


def test_sessions_do_not_share_staging() -> None:
    intake, _ = make_intake()
    intake.stage("s1", LIMITED)

    assert intake.pending_guest_ids("s2") == []
    intake.discard("s2")
    assert intake.pending_guest_ids("s1") == ["g2"]


def test_describe_echoes_the_flags_without_exposing_them_in_state() -> None:
    intake, _ = make_intake()
    intake.stage(
        "s1",
        StagedAccessibility(
            guest_id="g2", daily_walking_limit_minutes=45, rest_frequency_minutes=30
        ),
    )

    assert intake.describe("s1") == {
        "g2": "a 45-minute daily walking limit, a rest break every 30 minutes"
    }


class _RecordingCursor:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    def execute(self, query: str, params: Any = None) -> None:
        self._statements.append(query)


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def transaction(self) -> Any:
        return nullcontext()

    @contextmanager
    def cursor(self, **_: Any) -> Any:
        yield _RecordingCursor(self.statements)


def test_session_store_refuses_to_persist_without_consent() -> None:
    conn = _RecordingConnection()
    store = PostgresSessionStore(conn, SessionMemory())  # type: ignore[arg-type]
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
