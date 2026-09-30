"""P0-11 Done-when 4: "A re-normalization command rebuilds normalized rows from raw payloads."

Snapshots are taken by the real collector (captured payloads, real Postgres),
then damaged or marked as built by an older normalizer, then rebuilt.
"""

import json
from datetime import timedelta

import factories
import psycopg
from capture import NOW, Provider, capture

from parkmind.services.clients.postgres.id_mapping_repository import (
    PostgresIdMappingRepository,
)
from parkmind.services.clients.postgres.snapshot_repository import (
    PostgresSnapshotRepository,
)
from parkmind.services.use_cases.collect_snapshot import SnapshotCollector
from parkmind.services.use_cases.renormalize_snapshots import (
    all_snapshot_ids,
    renormalize_snapshots,
    stale_snapshot_ids,
)


def _collect(conn: psycopg.Connection, *, minutes: int = 0) -> str:
    provider = Provider()
    result = SnapshotCollector(
        provider.parks(),
        provider.weather(),
        PostgresSnapshotRepository(conn),
        PostgresIdMappingRepository(conn),
    ).collect(now=NOW + timedelta(minutes=minutes))
    return result.snapshot_id


def _renormalize(conn: psycopg.Connection, ids: list[str], **kwargs):  # type: ignore[no-untyped-def]
    return renormalize_snapshots(
        PostgresSnapshotRepository(conn), PostgresIdMappingRepository(conn), ids, **kwargs
    )


def test_rebuilds_live_context_from_raw_payload(conn: psycopg.Connection) -> None:
    sid = _collect(conn)
    repo = PostgresSnapshotRepository(conn)
    original = repo.get(sid)
    # An "ID-mapping bug" left the stored LiveContext wrong (here: unreadable).
    conn.execute(
        "UPDATE snapshots SET live_context = %s::jsonb WHERE snapshot_id = %s",
        (json.dumps({"broken": True}), sid),
    )

    report = _renormalize(conn, [sid])

    assert [(o.snapshot_id, o.status) for o in report.outcomes] == [(sid, "rebuilt")]
    assert repo.get(sid) == original
    raw = repo.get_raw_payload(sid)
    assert raw is not None and raw["themeparks"]["live"] == capture("themeparks_live.json")
    meta = repo.get_meta(sid)
    assert meta is not None and meta.retrieved_at == NOW


def test_only_rows_below_the_target_version_are_selected(conn: psycopg.Connection) -> None:
    old, current = _collect(conn, minutes=0), _collect(conn, minutes=5)
    conn.execute("UPDATE snapshots SET normalizer_version = 2 WHERE snapshot_id = %s", (current,))
    repo = PostgresSnapshotRepository(conn)

    stale = stale_snapshot_ids(repo, target_version=2)
    report = _renormalize(conn, stale, target_version=2)

    assert stale == [old]
    assert report.count("rebuilt") == 1
    old_meta = repo.get_meta(old)
    assert old_meta is not None and old_meta.normalizer_version == 2
    assert stale_snapshot_ids(repo, target_version=2) == []


def test_rerunning_is_a_no_op(conn: psycopg.Connection) -> None:
    sid = _collect(conn)
    repo = PostgresSnapshotRepository(conn)

    report = _renormalize(conn, all_snapshot_ids(repo))

    assert [(o.snapshot_id, o.status) for o in report.outcomes] == [(sid, "unchanged")]


def test_one_bad_row_does_not_stop_the_rest(conn: psycopg.Connection) -> None:
    good = _collect(conn)
    # A hand-made snapshot whose raw payload isn't a collector payload.
    PostgresSnapshotRepository(conn).save(
        factories.live_context(snapshot_id="handmade", retrieved_at=NOW - timedelta(days=1)),
        {"liveData": []},
        [],
        normalizer_version=1,
    )

    report = _renormalize(conn, ["handmade", good, "ghost"], target_version=2)

    by_id = {o.snapshot_id: o for o in report.outcomes}
    assert by_id[good].status == "rebuilt"
    assert by_id["handmade"].status == "failed" and "raw_schema" in (by_id["handmade"].reason or "")
    assert by_id["ghost"].status == "failed" and by_id["ghost"].reason == "no such snapshot"
