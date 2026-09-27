"""P0-12 snapshots (Architecture section 41 [C21]): raw provider payload beside the
normalized rows. P0-11's collector builds idempotency, age and re-normalization on
top of these guarantees.
"""

from datetime import timedelta

import factories
import psycopg
import pytest

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

    stored = repo.save(snapshot, RAW, [DataSource.THEMEPARKS_WIKI, DataSource.OPEN_METEO], normalizer_version=1)

    assert stored is True
    assert repo.get("snap_1") == snapshot
    assert repo.get_raw_payload("snap_1") == RAW
    assert repo.get_data_sources("snap_1") == [DataSource.THEMEPARKS_WIKI, DataSource.OPEN_METEO]


def test_snapshot_keeps_its_retrieval_time_timezone_aware(conn: psycopg.Connection) -> None:
    repo = PostgresSnapshotRepository(conn)
    repo.save(factories.live_context(), RAW, [DataSource.THEMEPARKS_WIKI], normalizer_version=1)

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

    assert repo.save(first, RAW, [DataSource.THEMEPARKS_WIKI], normalizer_version=1) is True
    assert repo.save(second, {"different": True}, [DataSource.CACHE], normalizer_version=1) is False

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

    repo.save(newer, RAW, [DataSource.THEMEPARKS_WIKI], normalizer_version=1)
    repo.save(older, RAW, [DataSource.THEMEPARKS_WIKI], normalizer_version=1)  # inserted last, retrieved first

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

    repo.save(factories.live_context(), RAW, [], normalizer_version=1)

    assert repo.get_data_sources("snap_1") == []


# ----------------------------------------------------- P0-11: normalizer version


def test_meta_is_readable_without_parsing_live_context(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresSnapshotRepository(conn)
    repo.save(
        factories.live_context(),
        RAW,
        [DataSource.THEMEPARKS_WIKI],
        normalizer_version=3,
    )
    conn.execute("UPDATE snapshots SET live_context = '{\"broken\": true}'::jsonb")

    meta = repo.get_meta("snap_1")

    assert meta is not None
    assert (meta.snapshot_id, meta.normalizer_version) == ("snap_1", 3)
    assert meta.data_sources == [DataSource.THEMEPARKS_WIKI]
    assert meta.retrieved_at == factories.NOW
    assert repo.get_meta("nope") is None


def test_replace_normalized_rewrites_only_live_context_and_version(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresSnapshotRepository(conn)
    original = factories.live_context()
    repo.save(original, RAW, [DataSource.THEMEPARKS_WIKI], normalizer_version=1)
    rebuilt = original.model_copy(update={"statuses": {}})

    assert repo.replace_normalized(rebuilt, normalizer_version=2) is True

    assert repo.get("snap_1") == rebuilt
    assert repo.get_raw_payload("snap_1") == RAW
    assert repo.get_data_sources("snap_1") == [DataSource.THEMEPARKS_WIKI]
    meta = repo.get_meta("snap_1")
    assert meta is not None and meta.normalizer_version == 2


def test_replace_normalized_never_moves_a_snapshot_in_time(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresSnapshotRepository(conn)
    repo.save(factories.live_context(), RAW, [], normalizer_version=1)
    moved = factories.live_context(retrieved_at=factories.NOW + timedelta(hours=1))

    with pytest.raises(ValueError, match="retrieved_at"):
        repo.replace_normalized(moved, normalizer_version=2)
    assert (
        repo.replace_normalized(
            factories.live_context(snapshot_id="nope"), normalizer_version=2
        )
        is False
    )


def test_recent_ids_are_newest_first_and_limited(conn: psycopg.Connection) -> None:
    repo = PostgresSnapshotRepository(conn)
    for hours in (0, 2, 1):
        repo.save(
            factories.live_context(
                snapshot_id=f"snap_{hours}",
                retrieved_at=factories.NOW + timedelta(hours=hours),
            ),
            RAW,
            [],
            normalizer_version=1,
        )

    assert repo.recent_ids(2) == ["snap_2", "snap_1"]
    assert repo.recent_ids(0) == []


def test_ids_below_version_finds_rows_built_by_an_older_normalizer(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresSnapshotRepository(conn)
    for sid, version, hours in (("old_b", 1, 1), ("old_a", 1, 0), ("current", 2, 2)):
        repo.save(
            factories.live_context(
                snapshot_id=sid, retrieved_at=factories.NOW + timedelta(hours=hours)
            ),
            RAW,
            [],
            normalizer_version=version,
        )

    assert repo.ids_below_version(2) == ["old_a", "old_b"]
    assert repo.ids_below_version(1) == []


def test_normalizer_version_must_be_positive(conn: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="normalizer_version"):
        PostgresSnapshotRepository(conn).save(
            factories.live_context(), RAW, [], normalizer_version=0
        )


def test_latest_valid_snapshot_skips_a_row_that_no_longer_validates(
    conn: psycopg.Connection,
) -> None:
    """P0-11 Done-when 3 against the real table: a corrupt newest row is passed over."""
    from parkmind.services.use_cases.latest_snapshot import latest_valid_snapshot

    repo = PostgresSnapshotRepository(conn)
    for sid, minutes in (("older", 20), ("newest", 1)):
        repo.save(
            factories.live_context(snapshot_id=sid, retrieved_at=factories.NOW - timedelta(minutes=minutes)),
            RAW,
            [],
            normalizer_version=1,
        )
    conn.execute("UPDATE snapshots SET live_context = '{\"broken\": true}'::jsonb WHERE snapshot_id = 'newest'")

    latest = latest_valid_snapshot(repo, now=factories.NOW)

    assert latest is not None
    assert latest.live_context.snapshot_id == "older"
    assert latest.skipped == ["newest"]
    assert latest.age == timedelta(minutes=20)
