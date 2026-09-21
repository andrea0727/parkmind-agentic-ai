"""P0-12 snapshots (Architecture section 41 [C21]): raw provider payload beside the
normalized rows. P0-11's collector builds idempotency, age and re-normalization on
top of these guarantees.
"""

from datetime import timedelta

import factories
import psycopg

from parkmind.core.contracts import DataSource
from parkmind.services.clients.postgres.snapshot_repository import (
    PostgresSnapshotRepository,
)

RAW = {
    "liveData": [
        {"id": "e39b831b", "status": "OPERATING", "queue": {"STANDBY": {"waitTime": 25}}},
        {"id": "888fb4a4", "status": "DOWN", "queue": None},
    ],
    "name": "Magic Kingdom — Walt Disney World®",
    "n": 1.5,
}


def test_snapshot_round_trips_with_raw_payload_and_sources(conn: psycopg.Connection) -> None:
    repo = PostgresSnapshotRepository(conn)
    snapshot = factories.live_context(snapshot_id="snap_1")

    stored = repo.save(snapshot, RAW, [DataSource.THEMEPARKS_WIKI, DataSource.OPEN_METEO])

    assert stored is True
    assert repo.get("snap_1") == snapshot
    assert repo.get_raw_payload("snap_1") == RAW
    assert repo.get_data_sources("snap_1") == [DataSource.THEMEPARKS_WIKI, DataSource.OPEN_METEO]


def test_snapshot_keeps_its_retrieval_time_timezone_aware(conn: psycopg.Connection) -> None:
    repo = PostgresSnapshotRepository(conn)
    repo.save(factories.live_context(), RAW, [DataSource.THEMEPARKS_WIKI])

    stored = repo.get("snap_1")

    assert stored is not None
    assert stored.retrieved_at.tzinfo is not None
    assert stored.retrieved_at == factories.NOW


def test_saving_the_same_snapshot_twice_leaves_the_first_row_untouched(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresSnapshotRepository(conn)
    first = factories.live_context(snapshot_id="snap_1")
    second = factories.live_context(snapshot_id="snap_1", retrieved_at=factories.NOW + timedelta(hours=1))

    assert repo.save(first, RAW, [DataSource.THEMEPARKS_WIKI]) is True
    assert repo.save(second, {"different": True}, [DataSource.CACHE]) is False

    assert repo.get("snap_1") == first
    assert repo.get_raw_payload("snap_1") == RAW
    assert repo.get_data_sources("snap_1") == [DataSource.THEMEPARKS_WIKI]
    count = conn.execute("SELECT count(*) AS n FROM snapshots").fetchone()
    assert count is not None and count["n"] == 1


def test_latest_snapshot_is_the_most_recently_retrieved_not_the_last_inserted(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresSnapshotRepository(conn)
    newer = factories.live_context(
        snapshot_id="snap_new", retrieved_at=factories.NOW + timedelta(minutes=5)
    )
    older = factories.live_context(snapshot_id="snap_old")

    repo.save(newer, RAW, [DataSource.THEMEPARKS_WIKI])
    repo.save(older, RAW, [DataSource.THEMEPARKS_WIKI])  # inserted last, retrieved first

    latest = repo.get_latest()
    assert latest is not None and latest.snapshot_id == "snap_new"


def test_unknown_or_missing_snapshots_are_none(conn: psycopg.Connection) -> None:
    repo = PostgresSnapshotRepository(conn)

    assert repo.get_latest() is None
    assert repo.get("nope") is None
    assert repo.get_raw_payload("nope") is None
    assert repo.get_data_sources("nope") is None


def test_a_snapshot_may_be_collected_from_no_source_at_all(conn: psycopg.Connection) -> None:
    repo = PostgresSnapshotRepository(conn)

    repo.save(factories.live_context(), RAW, [])

    assert repo.get_data_sources("snap_1") == []
