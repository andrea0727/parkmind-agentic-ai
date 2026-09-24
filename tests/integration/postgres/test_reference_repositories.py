"""P0-12: attraction data, ID mappings and provenance repositories."""

from datetime import UTC, date, datetime, timedelta

import factories
import psycopg
import pytest

from parkmind.core.contracts import PARK_TZ, AttractionCategory
from parkmind.services.clients.postgres.attraction_repository import (
    PostgresAttractionRepository,
)
from parkmind.services.clients.postgres.id_mapping_repository import (
    PostgresIdMappingRepository,
)
from parkmind.services.clients.postgres.provenance_repository import (
    PostgresProvenanceRepository,
)
from parkmind.services.ports import IdMappingConflictError, ProvenanceConflictError

# ----------------------------------------------------------------------- attractions


def test_catalog_round_trips_in_node_id_order_and_is_scoped_per_park(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresAttractionRepository(conn)
    b = factories.attraction(node_id="b", name="B", category=AttractionCategory.FAMILY)
    a = factories.attraction(node_id="a", name="A")

    repo.save_catalog("mk", [b, a])
    repo.save_catalog("epcot", [factories.attraction(node_id="z", name="Z")])

    assert repo.list_attractions("mk") == [a, b]
    assert [x.node_id for x in repo.list_attractions("epcot")] == ["z"]
    assert repo.get_attraction("a") == a
    assert repo.get_attraction("nope") is None


def test_resaving_the_catalog_updates_attractions_in_place(conn: psycopg.Connection) -> None:
    repo = PostgresAttractionRepository(conn)
    repo.save_catalog("mk", [factories.attraction(node_id="a", typical_wait_minutes=60)])

    repo.save_catalog("mk", [factories.attraction(node_id="a", typical_wait_minutes=45)])

    stored = repo.list_attractions("mk")
    assert len(stored) == 1 and stored[0].typical_wait_minutes == 45


def test_schedule_is_keyed_by_park_local_date(conn: psycopg.Connection) -> None:
    repo = PostgresAttractionRepository(conn)
    park = factories.park()

    repo.save_schedule(park)

    assert repo.get_schedule("mk", date(2026, 9, 16)) == park
    assert repo.get_schedule("mk", date(2026, 9, 17)) is None
    assert repo.get_schedule("other", date(2026, 9, 16)) is None


def test_schedule_date_is_the_park_local_date_not_the_utc_date(conn: psycopg.Connection) -> None:
    repo = PostgresAttractionRepository(conn)
    # 22:00 New York on the 16th, handed over in UTC where it is already the
    # 17th: a naive `.date()` would file it under the wrong day.
    late = factories.park(
        opening_time=datetime(2026, 9, 17, 2, 0, tzinfo=UTC),
        closing_time=datetime(2026, 9, 17, 6, 0, tzinfo=UTC),
    )

    repo.save_schedule(late)

    assert repo.get_schedule("mk", date(2026, 9, 16)) == late
    assert repo.get_schedule("mk", date(2026, 9, 17)) is None


# ------------------------------------------------------------------------ id mapping

SEEN = datetime(2026, 9, 16, 10, 0, tzinfo=PARK_TZ)


def test_mapping_resolves_and_traces_back_to_every_provider_id(conn: psycopg.Connection) -> None:
    repo = PostgresIdMappingRepository(conn)

    repo.record("themeparks_wiki", "tp-1", "attraction", "attr_space_mountain", seen_at=SEEN)
    repo.record("queue_times", "qt-77", "attraction", "attr_space_mountain", seen_at=SEEN)

    assert repo.resolve("themeparks_wiki", "tp-1", "attraction") == "attr_space_mountain"
    assert repo.provider_ids_for("attr_space_mountain") == [
        ("queue_times", "qt-77", "attraction"),
        ("themeparks_wiki", "tp-1", "attraction"),
    ]
    assert repo.resolve("themeparks_wiki", "unknown", "attraction") is None


def test_the_same_mapping_seen_again_only_moves_last_seen_forward(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresIdMappingRepository(conn)
    repo.record("themeparks_wiki", "tp-1", "attraction", "attr_1", seen_at=SEEN)

    repo.record("themeparks_wiki", "tp-1", "attraction", "attr_1", seen_at=SEEN + timedelta(days=1))
    repo.record("themeparks_wiki", "tp-1", "attraction", "attr_1", seen_at=SEEN - timedelta(days=1))

    row = conn.execute(
        "SELECT first_seen_at, last_seen_at FROM id_mapping WHERE provider_id = 'tp-1'"
    ).fetchone()
    assert row is not None
    assert row["first_seen_at"] == SEEN - timedelta(days=1)
    assert row["last_seen_at"] == SEEN + timedelta(days=1)


def test_remapping_a_provider_id_to_a_different_internal_id_is_surfaced_not_merged(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresIdMappingRepository(conn)
    repo.record("themeparks_wiki", "tp-1", "attraction", "attr_1", seen_at=SEEN)

    with pytest.raises(IdMappingConflictError):
        repo.record("themeparks_wiki", "tp-1", "attraction", "attr_OTHER", seen_at=SEEN)

    assert repo.resolve("themeparks_wiki", "tp-1", "attraction") == "attr_1"


def test_entity_kind_separates_provider_id_namespaces(conn: psycopg.Connection) -> None:
    repo = PostgresIdMappingRepository(conn)

    repo.record("themeparks_wiki", "42", "attraction", "attr_1", seen_at=SEEN)
    repo.record("themeparks_wiki", "42", "land", "land_1", seen_at=SEEN)

    assert repo.resolve("themeparks_wiki", "42", "attraction") == "attr_1"
    assert repo.resolve("themeparks_wiki", "42", "land") == "land_1"


# ------------------------------------------------------------------------ provenance


def test_provenance_round_trips_and_is_findable_by_snapshot(conn: psycopg.Connection) -> None:
    repo = PostgresProvenanceRepository(conn)
    prov = factories.provenance(snapshot_id="snap_9")

    repo.record("PLAN", "plan_1", prov)
    repo.record("PROPOSAL", "prop_1", prov)
    repo.record("PLAN", "plan_other", factories.provenance(snapshot_id="snap_other"))

    assert repo.get("PLAN", "plan_1") == prov
    assert repo.get("PLAN", "missing") is None
    assert repo.subjects_for_snapshot("snap_9") == [("PLAN", "plan_1"), ("PROPOSAL", "prop_1")]


def test_recording_identical_provenance_again_is_a_no_op(conn: psycopg.Connection) -> None:
    repo = PostgresProvenanceRepository(conn)
    prov = factories.provenance()

    repo.record("PLAN", "plan_1", prov)
    repo.record("PLAN", "plan_1", prov)

    assert repo.get("PLAN", "plan_1") == prov


def test_recording_different_provenance_for_the_same_subject_is_refused(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresProvenanceRepository(conn)
    repo.record("PLAN", "plan_1", factories.provenance(optimizer_strategy="greedy_repair"))

    with pytest.raises(ProvenanceConflictError):
        repo.record("PLAN", "plan_1", factories.provenance(optimizer_strategy="something_else"))
