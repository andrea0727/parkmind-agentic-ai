"""P0-10 against the real ``id_mapping`` table (backlog Done-when 1 and 2).

1. "The same attraction has a stable internal ID across snapshots."
2. "Provider IDs can be traced back through provenance."
"""

import copy
import json
from datetime import timedelta
from pathlib import Path

import factories
import psycopg

from parkmind.core.contracts import DataSource
from parkmind.services.clients.postgres.id_mapping_repository import (
    PostgresIdMappingRepository,
)
from parkmind.services.clients.postgres.plan_repository import PostgresPlanRepository
from parkmind.services.clients.postgres.snapshot_repository import (
    PostgresSnapshotRepository,
)
from parkmind.services.clients.themeparks_normalize import parse_catalog, parse_live
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
)
from parkmind.services.use_cases.id_resolution import IdResolver

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "themeparks"
THEMEPARKS = DataSource.THEMEPARKS_WIKI
SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
NOW = factories.NOW


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_internal_ids_are_stable_across_snapshots_in_postgres(
    conn: psycopg.Connection, migrated_database_url: str
) -> None:
    first = _fixture("live_magic_kingdom.json")
    later = copy.deepcopy(first)
    for entity in later["liveData"]:
        entity["lastUpdated"] = "2026-09-16T18:00:00Z"

    one = IdResolver(PostgresIdMappingRepository(conn)).resolve_live(
        THEMEPARKS, parse_live(first), seen_at=NOW
    )
    # A later collector run: new process, new connection, same table.
    with psycopg.connect(migrated_database_url) as other:
        two = IdResolver(PostgresIdMappingRepository(other)).resolve_live(
            THEMEPARKS, parse_live(later), seen_at=NOW + timedelta(hours=4)
        )

    assert set(one.entities) == set(two.entities) != set()
    assert one.issues == two.issues == []
    rows = conn.execute(
        "SELECT count(*) AS n, min(first_seen_at) AS first, max(last_seen_at) AS last FROM id_mapping"
    ).fetchone()
    assert rows is not None
    assert rows["n"] == len(one.entities)  # the second run added no mapping
    assert (rows["first"], rows["last"]) == (NOW, NOW + timedelta(hours=4))


def test_catalog_and_live_resolve_to_the_same_internal_ids(conn: psycopg.Connection) -> None:
    resolver = IdResolver(PostgresIdMappingRepository(conn))

    catalog = resolver.resolve_catalog(
        THEMEPARKS,
        parse_catalog(_fixture("children_magic_kingdom.json"), MAGIC_KINGDOM_ATTRACTION_METADATA),
        seen_at=NOW,
    )
    live = resolver.resolve_live(THEMEPARKS, parse_live(_fixture("live_magic_kingdom.json")), seen_at=NOW)

    assert {a.node_id for a in catalog.attractions} <= set(live.entities)


def test_plan_stop_traces_back_to_provider_id(conn: psycopg.Connection) -> None:
    """Plan stop -> Provenance.snapshot_id -> raw payload, and internal id -> provider ids."""
    raw = _fixture("live_magic_kingdom.json")
    resolver = IdResolver(PostgresIdMappingRepository(conn))
    live = resolver.resolve_live(THEMEPARKS, parse_live(raw), seen_at=NOW)
    PostgresSnapshotRepository(conn).save(
        factories.live_context(snapshot_id="snap_trace"), raw, [THEMEPARKS]
    )
    plan = factories.plan(
        plan_id="plan_trace",
        stops=[factories.stop(node_id=SPACE_MOUNTAIN)],
        provenance=factories.provenance(snapshot_id="snap_trace"),
    )
    PostgresPlanRepository(conn).save("t1", plan)

    stored = PostgresPlanRepository(conn).get("plan_trace")
    assert stored is not None
    node_id = stored.stops[0].node_id
    assert node_id in live.entities

    # internal id -> every provider id recorded for it
    assert resolver.trace(node_id) == [("themeparks_wiki", SPACE_MOUNTAIN, "attraction")]
    # provenance -> the exact provider payload the plan was built from
    payload = PostgresSnapshotRepository(conn).get_raw_payload(stored.provenance.snapshot_id)
    assert payload is not None
    assert SPACE_MOUNTAIN in {entity["id"] for entity in payload["liveData"]}
