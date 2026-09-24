"""P0-12 Done-when: "Seed data can populate a reproducible development scenario."

Reproducible means: the same content in two independent fresh databases, and no
change when the seed is run again.
"""

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from parkmind.core.contracts import ApprovalStatus, PlanningPace
from parkmind.services.clients.postgres.attraction_repository import (
    PostgresAttractionRepository,
)
from parkmind.services.clients.postgres.behavior_log_repository import (
    PostgresBehaviorLogRepository,
)
from parkmind.services.clients.postgres.event_repository import PostgresEventRepository
from parkmind.services.clients.postgres.execution_state_repository import (
    PostgresExecutionStateRepository,
)
from parkmind.services.clients.postgres.guest_repository import PostgresGuestRepository
from parkmind.services.clients.postgres.plan_repository import PostgresPlanRepository
from parkmind.services.clients.postgres.profile_repository import (
    PostgresProfileRepository,
)
from parkmind.services.clients.postgres.proposal_repository import (
    PostgresProposalRepository,
)
from parkmind.services.clients.postgres.seed import (
    ACTIVE_PLAN_ID,
    ACTIVE_PROPOSAL_ID,
    CANDIDATE_PLAN_ID,
    CANDIDATE_PROPOSAL_ID,
    MAXIMIZER,
    PARK_ID,
    RELAXED_ADULT,
    RELAXED_CHILD,
    SNAPSHOT_ID,
    THREAD_ID,
    seed_dev_scenario,
)
from parkmind.services.clients.postgres.session_store import (
    PostgresSessionStore,
    SessionMemory,
)
from parkmind.services.clients.postgres.snapshot_repository import (
    PostgresSnapshotRepository,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# Written by the database, not by the scenario.
_BOOKKEEPING = ("created_at", "updated_at", "recorded_at", "stored_at")
SEEDED_TABLES = {
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
    "provenance",
    "accessibility_requirements",
}


def _dump(url: str) -> dict[str, list[str]]:
    """Every row of every table as JSON text, minus database-written bookkeeping."""
    strip = sql.SQL("").join(sql.SQL(" - {}").format(sql.Literal(c)) for c in _BOOKKEEPING)
    dump: dict[str, list[str]] = {}
    with psycopg.connect(url, row_factory=dict_row) as reader:
        tables = reader.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
        ).fetchall()
        for table in tables:
            query = sql.SQL("SELECT (to_jsonb(t){} )::text AS doc FROM {} AS t ORDER BY 1").format(
                strip, sql.Identifier(table["tablename"])
            )
            dump[table["tablename"]] = [row["doc"] for row in reader.execute(query).fetchall()]
    return dump


def _seed(url: str) -> None:
    with psycopg.connect(url) as conn:
        seed_dev_scenario(conn)


def test_seed_dev_scenario_is_reproducible(make_database: Callable[[], str]) -> None:
    first, second = make_database(), make_database()

    _seed(first)
    _seed(second)

    dump_first = _dump(first)
    assert dump_first == _dump(second)
    populated = {table for table, rows in dump_first.items() if rows}
    assert SEEDED_TABLES <= populated


def test_reseeding_changes_nothing(make_database: Callable[[], str]) -> None:
    url = make_database()
    _seed(url)
    once = _dump(url)

    _seed(url)

    assert _dump(url) == once


def test_seeded_rows_validate_as_contracts(conn: psycopg.Connection) -> None:
    seed_dev_scenario(conn)

    guests = PostgresGuestRepository(conn).list_all()
    assert {g.guest_id for g in guests} == {RELAXED_ADULT, RELAXED_CHILD, MAXIMIZER}

    # Opposing profiles (section 38), with stated values preserved.
    profiles = PostgresProfileRepository(conn)
    relaxed, maximizer = profiles.get_latest(RELAXED_ADULT), profiles.get_latest(MAXIMIZER)
    assert relaxed is not None and maximizer is not None
    assert (relaxed.pace, maximizer.pace) == (PlanningPace.RELAXED, PlanningPace.MAXIMIZER)
    assert relaxed.queue_tolerance.stated_value == 0.3

    # Active vs candidate are distinct, and the two plans share no stop (Lift 1.00).
    plans, proposals = PostgresPlanRepository(conn), PostgresProposalRepository(conn)
    active = plans.get_active(THREAD_ID)
    candidate = plans.get(CANDIDATE_PLAN_ID)
    assert active is not None and active.plan_id == ACTIVE_PLAN_ID
    assert candidate is not None and candidate.plan_id != active.plan_id
    assert {s.node_id for s in active.stops}.isdisjoint({s.node_id for s in candidate.stops})
    pending = proposals.list_pending(THREAD_ID)
    assert [p.proposal_id for p in pending] == [CANDIDATE_PROPOSAL_ID]
    approved = proposals.get(ACTIVE_PROPOSAL_ID)
    assert approved is not None and approved.approval_status is ApprovalStatus.APPROVED

    # Persisted accessibility only, retrievable in any later session.
    assert PostgresSessionStore(conn, SessionMemory()).get("later_session", RELAXED_ADULT) is not None

    # Park data and the snapshot with its raw payload.
    attractions = PostgresAttractionRepository(conn)
    assert len(attractions.list_attractions(PARK_ID)) == 7
    assert attractions.get_schedule(PARK_ID, date(2026, 9, 16)) is not None
    snapshots = PostgresSnapshotRepository(conn)
    latest = snapshots.get_latest()
    assert latest is not None and latest.snapshot_id == SNAPSHOT_ID
    raw = snapshots.get_raw_payload(SNAPSHOT_ID)
    assert raw is not None and len(raw["liveData"]) == 7

    # Progress, behavior and events.
    assert PostgresExecutionStateRepository(conn).get(ACTIVE_PLAN_ID) is not None
    assert len(PostgresBehaviorLogRepository(conn).get(MAXIMIZER).entries) == 1
    assert len(PostgresEventRepository(conn).list_for_thread(THREAD_ID)) == 1


def _run_seed_script(database_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "seed_db.py")],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )


def test_seed_script_seeds_the_database_named_by_database_url(
    make_database: Callable[[], str],
) -> None:
    url = make_database()

    result = _run_seed_script(url)

    assert result.returncode == 0, result.stderr
    assert _dump(url)["guests"], "the script reported success but seeded nothing"


def test_seed_script_fails_cleanly_when_the_schema_is_missing(
    empty_database_url: str,
) -> None:
    result = _run_seed_script(empty_database_url)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
