"""P0-11 Done-when 1 and 2, against the real ``snapshots`` and ``id_mapping`` tables.

1. "Re-running the same snapshot does not create unintended duplicates."
2. "Each snapshot has retrieval time, provenance and raw payload."
"""

from datetime import timedelta

import psycopg
from capture import NOW, Provider, capture

from parkmind.core.contracts import DataSource
from parkmind.services.clients.normalization import NORMALIZER_VERSION
from parkmind.services.clients.postgres.id_mapping_repository import (
    PostgresIdMappingRepository,
)
from parkmind.services.clients.postgres.snapshot_repository import (
    PostgresSnapshotRepository,
)
from parkmind.services.use_cases.collect_snapshot import SnapshotCollector


def _collector(conn: psycopg.Connection, provider: Provider) -> SnapshotCollector:
    return SnapshotCollector(
        provider.parks(),
        provider.weather(),
        PostgresSnapshotRepository(conn),
        PostgresIdMappingRepository(conn),
    )


def _count(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT count(*) AS n FROM snapshots").fetchone()
    assert row is not None
    return int(row["n"])


def test_rerun_in_same_window_writes_one_row(conn: psycopg.Connection) -> None:
    provider = Provider()
    first = _collector(conn, provider).collect(now=NOW)
    # A second process (new collector, same database) fires in the same window.
    second = _collector(conn, provider).collect(now=NOW + timedelta(minutes=2))

    assert (first.created, second.created) == (True, False)
    assert _count(conn) == 1


def test_next_window_is_a_new_snapshot(conn: psycopg.Connection) -> None:
    provider = Provider()
    collector = _collector(conn, provider)

    first = collector.collect(now=NOW)
    later = collector.collect(now=NOW + timedelta(minutes=5))

    assert later.created is True and later.snapshot_id != first.snapshot_id
    assert _count(conn) == 2
    latest = PostgresSnapshotRepository(conn).get_latest()
    assert latest is not None and latest.snapshot_id == later.snapshot_id


def test_snapshot_stores_raw_payload_sources_and_retrieved_at(
    conn: psycopg.Connection,
) -> None:
    result = _collector(conn, Provider()).collect(now=NOW)
    repo = PostgresSnapshotRepository(conn)

    raw = repo.get_raw_payload(result.snapshot_id)
    meta = repo.get_meta(result.snapshot_id)
    stored = repo.get(result.snapshot_id)

    assert raw is not None and meta is not None and stored is not None
    assert raw["themeparks"]["live"] == capture("themeparks_live.json")
    assert raw["open_meteo"]["hourly"] == capture("open_meteo_hourly.json")
    assert meta.retrieved_at == NOW
    assert meta.data_sources == [DataSource.THEMEPARKS_WIKI, DataSource.OPEN_METEO]
    assert meta.normalizer_version == NORMALIZER_VERSION
    assert stored == result.live_context


def test_every_seen_entity_is_recorded_in_id_mapping(conn: psycopg.Connection) -> None:
    """Provenance of ids: each live entity's provider id is traceable (P0-10)."""
    result = _collector(conn, Provider()).collect(now=NOW)
    assert result.live_context is not None

    row = conn.execute("SELECT count(*) AS n FROM id_mapping").fetchone()
    assert row is not None and row["n"] == len(result.live_context.statuses)


def test_weather_outage_still_writes_snapshot_without_open_meteo(
    conn: psycopg.Connection,
) -> None:
    result = _collector(conn, Provider(weather_status=503)).collect(now=NOW)

    meta = PostgresSnapshotRepository(conn).get_meta(result.snapshot_id)
    raw = PostgresSnapshotRepository(conn).get_raw_payload(result.snapshot_id)
    assert meta is not None and meta.data_sources == [DataSource.THEMEPARKS_WIKI]
    assert raw is not None and raw["open_meteo"] is None and raw["degraded"]
