"""P0-12 Done-when: "A `session_only` accessibility record is absent from every
persisted table after the session ends (test)."

Architecture section 12 / C19: the graph state carries ids only and the flags
live in a session store honoring `retention_policy` -- the checkpointer, which
persists state to Postgres, must never see them. The scan below reads every
table of the database, including the LangGraph checkpoint tables created by
`PostgresSaver.setup()`, so a future write path into any of them fails here.
"""

from datetime import timedelta

import factories
import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import sql
from psycopg.rows import dict_row

from parkmind.core.contracts import (
    AccessibilityRequirements,
    DataSource,
    MobilityRequirement,
    RideRestriction,
)
from parkmind.services.clients.postgres import migrate
from parkmind.services.clients.postgres.attraction_repository import (
    PostgresAttractionRepository,
)
from parkmind.services.clients.postgres.behavior_log_repository import (
    PostgresBehaviorLogRepository,
)
from parkmind.services.clients.postgres.event_repository import PostgresEventRepository
from parkmind.services.clients.postgres.guest_repository import PostgresGuestRepository
from parkmind.services.clients.postgres.id_mapping_repository import (
    PostgresIdMappingRepository,
)
from parkmind.services.clients.postgres.plan_repository import PostgresPlanRepository
from parkmind.services.clients.postgres.profile_repository import (
    PostgresProfileRepository,
)
from parkmind.services.clients.postgres.proposal_repository import (
    PostgresProposalRepository,
)
from parkmind.services.clients.postgres.session_store import (
    PostgresSessionStore,
    SessionMemory,
)
from parkmind.services.clients.postgres.snapshot_repository import (
    PostgresSnapshotRepository,
)
from parkmind.services.ports import ConsentRequiredError, NotFoundError

# Values that exist only in the session_only record below.
SESSION_ONLY = factories.accessibility(
    guest_id="g_session_only_sentinel",
    daily_walking_limit_minutes=137,
    rest_frequency_minutes=41,
    mobility_requirements=[MobilityRequirement.ECV],
    heat_sensitivity=True,
    ride_restrictions=[RideRestriction.NOT_RECOMMENDED_HEART_CONDITION],
    retention_policy="session_only",
)
SESSION_ONLY_TOKENS = [
    "g_session_only_sentinel",
    "NOT_RECOMMENDED_HEART_CONDITION",
    '"ECV"',
]

# Control: a persisted record with different values, to prove the scan can see.
PERSISTED_TOKEN = "NOT_RECOMMENDED_BACK_NECK"


def _persisted() -> AccessibilityRequirements:
    return factories.accessibility(
        guest_id="g_persisted",
        mobility_requirements=[MobilityRequirement.LIMITED_WALKING],
        ride_restrictions=[RideRestriction.NOT_RECOMMENDED_BACK_NECK],
        retention_policy="persisted",
    )


def _scan_every_table(url: str) -> dict[str, str]:
    """Every row of every user table as JSON text, read on a fresh connection so
    only committed state is visible."""
    scan: dict[str, str] = {}
    with psycopg.connect(url, row_factory=dict_row) as reader:
        tables = reader.execute(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
        ).fetchall()
        for table in tables:
            rows = reader.execute(
                sql.SQL("SELECT to_jsonb(t)::text AS doc FROM {} AS t").format(
                    sql.Identifier(table["schemaname"], table["tablename"])
                )
            ).fetchall()
            scan[table["tablename"]] = "\n".join(row["doc"] for row in rows)
    return scan


def _leaks(scan: dict[str, str], tokens: list[str]) -> dict[str, list[str]]:
    found = {table: [t for t in tokens if t in text] for table, text in scan.items()}
    return {table: hits for table, hits in found.items() if hits}


def _populate_realistic_data(conn: psycopg.Connection) -> None:
    """Rows in every repository, so the scan reads real content and not empty tables."""
    now = factories.NOW
    PostgresGuestRepository(conn).save(factories.guest(guest_id="g1"))
    PostgresProfileRepository(conn).save(factories.guest_profile(guest_id="g1"))
    PostgresBehaviorLogRepository(conn).append("g1", factories.behavior_entry())
    PostgresSnapshotRepository(conn).save(
        factories.live_context(), {"liveData": []}, [DataSource.THEMEPARKS_WIKI]
    )
    plans = PostgresPlanRepository(conn)
    plans.save("t1", factories.plan())
    PostgresProposalRepository(conn).save("t1", factories.proposal())
    PostgresEventRepository(conn).record("t1", factories.event())
    PostgresAttractionRepository(conn).save_catalog("mk", [factories.attraction()])
    PostgresIdMappingRepository(conn).record(
        "themeparks_wiki", "tp-1", "attraction", "a1", seen_at=now + timedelta(minutes=1)
    )


def test_session_only_record_absent_from_every_table_after_session_end(
    empty_database_url: str,
) -> None:
    migrate.upgrade(empty_database_url)
    with PostgresSaver.from_conn_string(empty_database_url) as saver:
        saver.setup()  # creates the LangGraph checkpoint tables

    memory = SessionMemory()  # one per process, shared by every request
    guest_id = SESSION_ONLY.guest_id

    # -- request 1 declares the need; request 2, on its own connection, sees it --
    with psycopg.connect(empty_database_url) as first_request:
        PostgresSessionStore(first_request, memory).put("sess_1", SESSION_ONLY)
    with psycopg.connect(empty_database_url) as conn:
        _populate_realistic_data(conn)
        store = PostgresSessionStore(conn, memory)

        # -- during the session: held, but written nowhere ---------------------
        assert store.get("sess_1", guest_id) == SESSION_ONLY
        during = _scan_every_table(empty_database_url)
        assert _leaks(during, SESSION_ONLY_TOKENS) == {}

        # -- after the session ends: gone, and still written nowhere -----------
        store.end_session("sess_1")
        assert store.get("sess_1", guest_id) is None
        after = _scan_every_table(empty_database_url)
        assert _leaks(after, SESSION_ONLY_TOKENS) == {}

        # -- neither the shared memory nor a fresh one can recover it ----------
        assert PostgresSessionStore(conn, memory).get("sess_1", guest_id) is None
        assert PostgresSessionStore(conn, SessionMemory()).get("sess_1", guest_id) is None

        # -- the scan is not blind: it covers checkpoint tables and real rows,
        #    and does see a persisted record where it belongs -------------------
        checkpoint_tables = {"checkpoints", "checkpoint_blobs", "checkpoint_writes"}
        assert checkpoint_tables <= set(after)
        populated = {table for table, text in after.items() if text}
        assert {"guests", "plans", "proposals", "snapshots", "events"} <= populated

        PostgresGuestRepository(conn).save(factories.guest(guest_id="g_persisted"))
        store.put("sess_2", _persisted())
        control = _scan_every_table(empty_database_url)
        assert _leaks(control, [PERSISTED_TOKEN]) == {
            "accessibility_requirements": [PERSISTED_TOKEN]
        }
        assert _leaks(control, SESSION_ONLY_TOKENS) == {}


def test_persisted_record_survives_the_end_of_a_session(conn: psycopg.Connection) -> None:
    PostgresGuestRepository(conn).save(factories.guest(guest_id="g1"))
    record = factories.accessibility(guest_id="g1", retention_policy="persisted")
    store = PostgresSessionStore(conn, SessionMemory())

    store.put("sess_1", record)
    store.end_session("sess_1")

    assert PostgresSessionStore(conn, SessionMemory()).get("another_session", "g1") == record


def test_persisting_updates_replace_the_previous_flags(conn: psycopg.Connection) -> None:
    PostgresGuestRepository(conn).save(factories.guest(guest_id="g1"))
    store = PostgresSessionStore(conn, SessionMemory())
    store.put("s", factories.accessibility(guest_id="g1", daily_walking_limit_minutes=60))

    store.put("s", factories.accessibility(guest_id="g1", daily_walking_limit_minutes=45))

    stored = store.get("s", "g1")
    assert stored is not None and stored.daily_walking_limit_minutes == 45
    count = conn.execute("SELECT count(*) AS n FROM accessibility_requirements").fetchone()
    assert count is not None and count["n"] == 1


def test_a_session_record_wins_over_the_persisted_one_for_that_session_only(
    conn: psycopg.Connection,
) -> None:
    PostgresGuestRepository(conn).save(factories.guest(guest_id="g1"))
    store = PostgresSessionStore(conn, SessionMemory())
    persisted = factories.accessibility(guest_id="g1", daily_walking_limit_minutes=90)
    session = factories.accessibility(
        guest_id="g1", daily_walking_limit_minutes=30, retention_policy="session_only"
    )
    store.put("s1", persisted)

    store.put("s1", session)

    assert store.get("s1", "g1") == session
    assert store.get("other", "g1") == persisted


def test_persisting_for_an_unknown_guest_is_refused(conn: psycopg.Connection) -> None:
    store = PostgresSessionStore(conn, SessionMemory())

    with pytest.raises(NotFoundError):
        store.put("s", factories.accessibility(guest_id="ghost", retention_policy="persisted"))


def test_persisting_without_consent_writes_nothing(conn: psycopg.Connection) -> None:
    PostgresGuestRepository(conn).save(factories.guest(guest_id="g1"))
    no_consent = factories.accessibility(
        guest_id="g1",
        daily_walking_limit_minutes=None,
        mobility_requirements=[],
        ride_restrictions=[],
        consent=False,
        retention_policy="persisted",
    )

    with pytest.raises(ConsentRequiredError):
        PostgresSessionStore(conn, SessionMemory()).put("s", no_consent)

    count = conn.execute("SELECT count(*) AS n FROM accessibility_requirements").fetchone()
    assert count is not None and count["n"] == 0
