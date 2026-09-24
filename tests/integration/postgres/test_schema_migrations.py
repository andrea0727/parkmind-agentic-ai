"""P0-12 Done-when: "Fresh database migrations create the complete MVP schema."

Also pins the structural half of the C19 privacy promise: the accessibility
table cannot physically hold a `session_only` or unconsented record.
"""

import json

import psycopg
import pytest
from alembic.util import CommandError
from psycopg import errors

from parkmind.services.clients.postgres import migrate

MVP_TABLES = {
    "guests",
    "guest_profiles",
    "behavior_entries",
    "plans",
    "proposals",
    "active_plans",
    "execution_states",
    "events",
    "snapshots",
    "attractions",
    "park_schedules",
    "id_mapping",
    "provenance",
    "accessibility_requirements",
}

# Owned by langgraph-checkpoint-postgres (PostgresSaver.setup()), never by our
# migrations: accessibility data must not have a place in checkpoint storage.
CHECKPOINT_TABLES = {
    "checkpoints",
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoint_migrations",
}


def _tables(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    return {row[0] for row in rows}


def test_fresh_database_migrates_to_complete_mvp_schema(empty_database_url: str) -> None:
    assert _tables(empty_database_url) == set()

    migrate.upgrade(empty_database_url)

    tables = _tables(empty_database_url)
    assert tables == MVP_TABLES | {"alembic_version"}
    assert not tables & CHECKPOINT_TABLES


def test_upgrade_refuses_a_database_with_leftover_v1_tables(empty_database_url: str) -> None:
    """Every existing dev volume holds the v1 init.sql tables. The upgrade must
    stop with an actionable message instead of a raw DuplicateTable traceback,
    and must leave the database exactly as it found it."""
    with psycopg.connect(empty_database_url, autocommit=True) as conn:
        conn.execute("CREATE TABLE guests (guest_id TEXT PRIMARY KEY, profile JSONB NOT NULL)")
        conn.execute("CREATE TABLE behavior_signals (id SERIAL PRIMARY KEY)")

    with pytest.raises(CommandError, match=r"docker compose down -v") as excinfo:
        migrate.upgrade(empty_database_url)

    assert "guests" in str(excinfo.value)
    assert _tables(empty_database_url) == {"guests", "behavior_signals"}


def test_upgrade_is_idempotent_when_rerun(empty_database_url: str) -> None:
    migrate.upgrade(empty_database_url)
    migrate.upgrade(empty_database_url)

    assert _tables(empty_database_url) == MVP_TABLES | {"alembic_version"}


def test_downgrade_then_upgrade_is_clean(empty_database_url: str) -> None:
    migrate.upgrade(empty_database_url)
    migrate.downgrade(empty_database_url)

    assert _tables(empty_database_url) == {"alembic_version"}

    migrate.upgrade(empty_database_url)

    assert _tables(empty_database_url) == MVP_TABLES | {"alembic_version"}


def _accessibility_payload(**overrides: object) -> str:
    payload = {
        "guest_id": "g1",
        "daily_walking_limit_minutes": 90,
        "rest_frequency_minutes": None,
        "mobility_requirements": [],
        "heat_sensitivity": False,
        "ride_restrictions": [],
        "consent": True,
        "retention_policy": "persisted",
    }
    payload.update(overrides)
    return json.dumps(payload)


_INSERT = (
    "INSERT INTO accessibility_requirements "
    "(guest_id, retention_policy, consent, payload) VALUES (%s, %s, %s, %s::jsonb)"
)


@pytest.mark.parametrize(
    ("retention_policy", "consent", "payload_overrides"),
    [
        ("session_only", True, {"retention_policy": "session_only"}),
        ("persisted", False, {"consent": False}),
        # Columns and payload must agree: neither may disguise the other.
        ("persisted", True, {"retention_policy": "session_only"}),
        ("session_only", True, {}),
    ],
    ids=[
        "session_only_everywhere",
        "no_consent_everywhere",
        "payload_says_session_only",
        "column_says_session_only",
    ],
)
def test_accessibility_table_rejects_session_only_and_unconsented_rows(
    conn: psycopg.Connection,
    retention_policy: str,
    consent: bool,
    payload_overrides: dict[str, object],
) -> None:
    conn.execute("INSERT INTO guests (guest_id, role) VALUES ('g1', 'adult')")

    with pytest.raises(errors.CheckViolation):
        conn.execute(
            _INSERT,
            ("g1", retention_policy, consent, _accessibility_payload(**payload_overrides)),
        )


def test_accessibility_table_accepts_a_consented_persisted_row(
    conn: psycopg.Connection,
) -> None:
    conn.execute("INSERT INTO guests (guest_id, role) VALUES ('g1', 'adult')")

    conn.execute(_INSERT, ("g1", "persisted", True, _accessibility_payload()))

    count = conn.execute("SELECT count(*) AS n FROM accessibility_requirements").fetchone()
    assert count is not None and count["n"] == 1


def test_deleting_a_guest_deletes_their_persisted_accessibility_record(
    conn: psycopg.Connection,
) -> None:
    conn.execute("INSERT INTO guests (guest_id, role) VALUES ('g1', 'adult')")
    conn.execute(_INSERT, ("g1", "persisted", True, _accessibility_payload()))

    conn.execute("DELETE FROM guests WHERE guest_id = 'g1'")

    count = conn.execute("SELECT count(*) AS n FROM accessibility_requirements").fetchone()
    assert count is not None and count["n"] == 0
