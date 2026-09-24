"""P0-12: every repository builds from a bare connection and round-trips its aggregate.

The connection is the only collaborator -- no HTTP layer, no graph, no LLM
(see tests/architecture/test_repository_independence.py).
"""

from datetime import timedelta

import factories
import psycopg
import pytest

from parkmind.core.contracts import BehaviorEventType, GuestRole, PreferenceSource
from parkmind.services.clients.postgres.behavior_log_repository import (
    PostgresBehaviorLogRepository,
)
from parkmind.services.clients.postgres.guest_repository import PostgresGuestRepository
from parkmind.services.clients.postgres.profile_repository import (
    PostgresProfileRepository,
)
from parkmind.services.ports import (
    NotFoundError,
    ProfileVersionConflictError,
    RepositoryUnavailableError,
)

# --------------------------------------------------------------------------- guests


def test_guest_round_trips_and_updates_in_place(conn: psycopg.Connection) -> None:
    repo = PostgresGuestRepository(conn)

    repo.save(factories.guest(guest_id="g1", height_cm=110.0, role=GuestRole.CHILD))
    repo.save(factories.guest(guest_id="g1", height_cm=112.5, role=GuestRole.CHILD))

    assert repo.get("g1") == factories.guest(
        guest_id="g1", height_cm=112.5, role=GuestRole.CHILD
    )


def test_guest_without_height_is_stored_as_none(conn: psycopg.Connection) -> None:
    repo = PostgresGuestRepository(conn)

    repo.save(factories.guest(guest_id="g1", height_cm=None))

    stored = repo.get("g1")
    assert stored is not None and stored.height_cm is None


def test_unknown_guest_is_none_and_guests_list_in_id_order(conn: psycopg.Connection) -> None:
    repo = PostgresGuestRepository(conn)
    for guest_id in ("g3", "g1", "g2"):
        repo.save(factories.guest(guest_id=guest_id))

    assert repo.get("nobody") is None
    assert [g.guest_id for g in repo.list_all()] == ["g1", "g2", "g3"]


# ------------------------------------------------------------------------- profiles


def _repos(conn: psycopg.Connection) -> tuple[PostgresGuestRepository, PostgresProfileRepository]:
    guests, profiles = PostgresGuestRepository(conn), PostgresProfileRepository(conn)
    guests.save(factories.guest(guest_id="g1"))
    guests.save(factories.guest(guest_id="g2"))
    return guests, profiles


def test_profiles_are_stored_independently_per_guest(conn: psycopg.Connection) -> None:
    _, profiles = _repos(conn)

    profiles.save(factories.guest_profile(guest_id="g1", pace="relaxed"))
    profiles.save(factories.guest_profile(guest_id="g2", pace="maximizer"))

    first, second = profiles.get_latest("g1"), profiles.get_latest("g2")
    assert first is not None and first.pace.value == "relaxed"
    assert second is not None and second.pace.value == "maximizer"


def test_profile_history_keeps_every_version_and_latest_wins(conn: psycopg.Connection) -> None:
    _, profiles = _repos(conn)
    v1 = factories.guest_profile(profile_version=1)
    v2 = factories.guest_profile(
        profile_version=2,
        queue_tolerance=factories.preference(
            0.2, source=PreferenceSource.LEARNED, stated_value=0.4
        ),
    )

    profiles.save(v1)
    profiles.save(v2)

    assert profiles.get_latest("g1") == v2
    assert profiles.get_version("g1", 1) == v1
    assert profiles.get_version("g1", 9) is None


def test_stated_value_survives_storage_of_a_learned_update(conn: psycopg.Connection) -> None:
    _, profiles = _repos(conn)
    learned = factories.preference(0.2, source=PreferenceSource.LEARNED, stated_value=0.4)

    profiles.save(factories.guest_profile(profile_version=1, queue_tolerance=learned))

    stored = profiles.get_latest("g1")
    assert stored is not None
    assert stored.queue_tolerance.source is PreferenceSource.LEARNED
    assert stored.queue_tolerance.stated_value == 0.4


@pytest.mark.parametrize("stale_version", [1, 2])
def test_profile_version_must_increase(conn: psycopg.Connection, stale_version: int) -> None:
    _, profiles = _repos(conn)
    profiles.save(factories.guest_profile(profile_version=2))

    with pytest.raises(ProfileVersionConflictError):
        profiles.save(factories.guest_profile(profile_version=stale_version, pace="relaxed"))


def test_resaving_an_identical_latest_profile_is_a_no_op(conn: psycopg.Connection) -> None:
    _, profiles = _repos(conn)
    profile = factories.guest_profile(profile_version=1)

    profiles.save(profile)
    profiles.save(profile)

    assert profiles.get_latest("g1") == profile


def test_profile_for_an_unknown_guest_is_refused(conn: psycopg.Connection) -> None:
    profiles = PostgresProfileRepository(conn)

    with pytest.raises(NotFoundError):
        profiles.save(factories.guest_profile(guest_id="ghost"))


# ----------------------------------------------------------------------- behavior log


def test_behavior_log_is_idempotent_on_entry_id(conn: psycopg.Connection) -> None:
    guests, _ = _repos(conn)
    log = PostgresBehaviorLogRepository(conn)
    entry = factories.behavior_entry(entry_id="e1")

    assert log.append("g1", entry) is True
    assert log.append("g1", entry) is False

    assert log.get("g1").entries == [entry]
    assert guests.get("g1") is not None


def test_same_entry_id_is_independent_across_guests(conn: psycopg.Connection) -> None:
    _repos(conn)
    log = PostgresBehaviorLogRepository(conn)

    assert log.append("g1", factories.behavior_entry(entry_id="e1")) is True
    assert log.append("g2", factories.behavior_entry(entry_id="e1")) is True


def test_behavior_log_is_ordered_oldest_first_and_keeps_aware_timestamps(
    conn: psycopg.Connection,
) -> None:
    _repos(conn)
    log = PostgresBehaviorLogRepository(conn)
    later = factories.behavior_entry(
        entry_id="e_late",
        event_type=BehaviorEventType.ATTRACTION_COMPLETED,
        timestamp=factories.NOW + timedelta(hours=1),
    )
    earlier = factories.behavior_entry(entry_id="e_early")

    log.append("g1", later)
    log.append("g1", earlier)

    stored = log.get("g1")
    assert [e.entry_id for e in stored.entries] == ["e_early", "e_late"]
    assert all(e.timestamp.tzinfo is not None for e in stored.entries)


def test_behavior_log_of_a_guest_with_no_entries_is_empty(conn: psycopg.Connection) -> None:
    _repos(conn)

    assert PostgresBehaviorLogRepository(conn).get("g1").entries == []


def test_behavior_entry_for_an_unknown_guest_is_refused(conn: psycopg.Connection) -> None:
    with pytest.raises(NotFoundError):
        PostgresBehaviorLogRepository(conn).append("ghost", factories.behavior_entry())


# ------------------------------------------------------------------------ availability


def test_a_closed_connection_raises_repository_unavailable_error(
    conn: psycopg.Connection,
) -> None:
    repo = PostgresGuestRepository(conn)
    conn.close()

    with pytest.raises(RepositoryUnavailableError):
        repo.get("g1")
