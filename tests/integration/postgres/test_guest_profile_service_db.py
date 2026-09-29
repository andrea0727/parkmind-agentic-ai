"""P0-14: GuestProfileService's merge/update semantics against real PostgreSQL.

`test_repositories_roundtrip.py` covers `PostgresProfileRepository`'s own
save/get contract; this file wires that repository into `GuestProfileService`
so its merge, no-op and validation behaviour -- proven against
`FakeProfileRepository` in `tests/unit/test_guest_profile_service.py` -- is
also proven against the real repository.
"""

import factories
import psycopg
import pytest

from parkmind.core.contracts import PreferenceSource, SensitivityKind, SensitivityLevel
from parkmind.services.clients.postgres.guest_repository import PostgresGuestRepository
from parkmind.services.clients.postgres.profile_repository import (
    PostgresProfileRepository,
)
from parkmind.services.personalization.guest_profile_service import (
    GuestProfileService,
    PreferenceUpdate,
    ProfileUpdate,
)
from parkmind.services.ports import NotFoundError

NOW = factories.NOW


def _service(conn: psycopg.Connection) -> GuestProfileService:
    PostgresGuestRepository(conn).save(factories.guest(guest_id="g1"))
    return GuestProfileService(PostgresProfileRepository(conn))


def test_update_merges_onto_the_latest_stored_profile(conn: psycopg.Connection) -> None:
    service = _service(conn)
    service.create(
        factories.guest_profile(
            guest_id="g1",
            sensitivities={SensitivityKind.LOUD_NOISE: SensitivityLevel.HIGH},
            thematic_affinity={"space": factories.preference(0.7)},
        )
    )

    updated = service.update(
        "g1",
        ProfileUpdate(
            queue_tolerance=PreferenceUpdate(
                value=0.9, source=PreferenceSource.LEARNED, confidence=0.7, updated_at=NOW
            )
        ),
    )

    assert updated.profile_version == 2
    assert updated.sensitivities == {SensitivityKind.LOUD_NOISE: SensitivityLevel.HIGH}
    assert updated.thematic_affinity["space"].value == 0.7
    assert updated.queue_tolerance.value == 0.9
    assert service.get("g1") == updated
    assert service.get_version("g1", 1).queue_tolerance.value != 0.9


@pytest.mark.parametrize("update", [ProfileUpdate(), ProfileUpdate(sensitivities={})])
def test_a_noop_update_does_not_persist_a_new_version(
    conn: psycopg.Connection, update: ProfileUpdate
) -> None:
    service = _service(conn)
    original = service.create(factories.guest_profile(guest_id="g1"))

    result = service.update("g1", update)

    assert result == original
    assert service.get_version("g1", 2) is None


def test_stated_value_survives_a_learned_update_round_trip(conn: psycopg.Connection) -> None:
    service = _service(conn)
    service.create(
        factories.guest_profile(
            guest_id="g1", queue_tolerance=factories.preference(0.4, stated_value=0.4)
        )
    )

    updated = service.update(
        "g1",
        ProfileUpdate(
            queue_tolerance=PreferenceUpdate(
                value=0.9, source=PreferenceSource.LEARNED, confidence=0.6, updated_at=NOW
            )
        ),
    )

    assert updated.queue_tolerance.source is PreferenceSource.LEARNED
    assert updated.queue_tolerance.stated_value == 0.4
    assert service.get("g1").queue_tolerance.stated_value == 0.4


def test_updating_a_guest_with_no_stored_profile_is_refused(conn: psycopg.Connection) -> None:
    PostgresGuestRepository(conn).save(factories.guest(guest_id="g1"))
    service = GuestProfileService(PostgresProfileRepository(conn))

    with pytest.raises(NotFoundError):
        service.update(
            "g1",
            ProfileUpdate(
                queue_tolerance=PreferenceUpdate(
                    value=0.9, source=PreferenceSource.LEARNED, confidence=0.6, updated_at=NOW
                )
            ),
        )
