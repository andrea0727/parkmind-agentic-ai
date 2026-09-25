"""GuestProfileService: storage, retrieval and merge semantics.

A fake ProfileRepository stands in for Postgres, mirroring its versioning
rules (append-only, strictly-increasing versions, idempotent re-save) so
these tests can focus purely on the service's merge behaviour.
"""

from datetime import timedelta

import factories
import pytest
from pydantic import ValidationError

from parkmind.core.contracts import (
    GuestProfile,
    PlanningPace,
    PreferenceSource,
    SensitivityKind,
    SensitivityLevel,
)
from parkmind.services.personalization.guest_profile_service import (
    GuestProfileService,
    PreferenceUpdate,
    ProfileUpdate,
)
from parkmind.services.ports import NotFoundError, ProfileVersionConflictError

NOW = factories.NOW


class FakeProfileRepository:
    """In-memory stand-in for PostgresProfileRepository's versioning rules."""

    def __init__(self, known_guests: set[str] | None = None) -> None:
        self._known_guests = known_guests or set()
        self._history: dict[str, dict[int, GuestProfile]] = {}

    def register_guest(self, guest_id: str) -> None:
        self._known_guests.add(guest_id)

    def save(self, profile: GuestProfile) -> None:
        if profile.guest_id not in self._known_guests:
            raise NotFoundError(f"unknown guest {profile.guest_id!r}")
        history = self._history.setdefault(profile.guest_id, {})
        if history:
            latest_version = max(history)
            if profile.profile_version == latest_version:
                if history[latest_version] == profile:
                    return
                raise ProfileVersionConflictError("profile_version must increase")
            if profile.profile_version < latest_version:
                raise ProfileVersionConflictError("profile_version must increase")
        history[profile.profile_version] = profile

    def get_latest(self, guest_id: str) -> GuestProfile | None:
        history = self._history.get(guest_id)
        return history[max(history)] if history else None

    def get_version(self, guest_id: str, profile_version: int) -> GuestProfile | None:
        return self._history.get(guest_id, {}).get(profile_version)


def _service_with_guests(*guest_ids: str) -> tuple[GuestProfileService, FakeProfileRepository]:
    repo = FakeProfileRepository(set(guest_ids))
    return GuestProfileService(repo), repo


def test_multiple_guests_are_stored_independently() -> None:
    service, _ = _service_with_guests("g1", "g2")
    service.create(factories.guest_profile(guest_id="g1", pace=PlanningPace.RELAXED))
    service.create(factories.guest_profile(guest_id="g2", pace=PlanningPace.MAXIMIZER))

    service.update(
        "g1",
        ProfileUpdate(
            queue_tolerance=PreferenceUpdate(
                value=0.9, source=PreferenceSource.LEARNED, confidence=0.7, updated_at=NOW
            )
        ),
    )

    assert service.get("g1").pace == PlanningPace.RELAXED
    assert service.get("g2").pace == PlanningPace.MAXIMIZER
    assert service.get("g2").profile_version == 1


def test_update_leaves_unrelated_dimensions_untouched() -> None:
    service, _ = _service_with_guests("g1")
    original = factories.guest_profile(
        guest_id="g1",
        sensitivities={SensitivityKind.LOUD_NOISE: SensitivityLevel.HIGH},
        thematic_affinity={"space": factories.preference(0.7)},
        preferred_categories=["THRILL"],
    )
    service.create(original)

    updated = service.update(
        "g1",
        ProfileUpdate(
            queue_tolerance=PreferenceUpdate(
                value=0.9, source=PreferenceSource.LEARNED, confidence=0.7, updated_at=NOW
            )
        ),
    )

    assert updated.walking_tolerance == original.walking_tolerance
    assert updated.pace == original.pace
    assert updated.planning_style == original.planning_style
    assert updated.sensitivities == original.sensitivities
    assert updated.thematic_affinity == original.thematic_affinity
    assert updated.preferred_categories == original.preferred_categories
    assert updated.queue_tolerance.value == 0.9


def test_sensitivities_and_thematic_affinity_merge_key_wise() -> None:
    service, _ = _service_with_guests("g1")
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
            sensitivities={SensitivityKind.HEIGHTS: SensitivityLevel.MEDIUM},
            thematic_affinity={
                "pirates": PreferenceUpdate(
                    value=0.3, source=PreferenceSource.STATED, confidence=0.9, updated_at=NOW
                )
            },
        ),
    )

    assert updated.sensitivities == {
        SensitivityKind.LOUD_NOISE: SensitivityLevel.HIGH,
        SensitivityKind.HEIGHTS: SensitivityLevel.MEDIUM,
    }
    assert set(updated.thematic_affinity) == {"space", "pirates"}
    assert updated.thematic_affinity["space"].value == 0.7
    assert updated.thematic_affinity["pirates"].value == 0.3


def test_confidence_values_are_validated() -> None:
    service, _ = _service_with_guests("g1")
    service.create(factories.guest_profile(guest_id="g1"))

    with pytest.raises(ValidationError):
        service.update(
            "g1",
            ProfileUpdate(
                queue_tolerance=PreferenceUpdate(
                    value=0.5, source=PreferenceSource.LEARNED, confidence=1.5, updated_at=NOW
                )
            ),
        )


def test_stated_value_survives_a_learned_update() -> None:
    service, _ = _service_with_guests("g1")
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
    assert updated.queue_tolerance.value == 0.9
    assert updated.queue_tolerance.stated_value == 0.4


def test_a_stated_update_can_set_a_new_stated_value() -> None:
    service, _ = _service_with_guests("g1")
    service.create(
        factories.guest_profile(
            guest_id="g1", queue_tolerance=factories.preference(0.4, stated_value=0.4)
        )
    )

    updated = service.update(
        "g1",
        ProfileUpdate(
            queue_tolerance=PreferenceUpdate(
                value=0.6, source=PreferenceSource.STATED, confidence=1.0, updated_at=NOW
            )
        ),
    )

    assert updated.queue_tolerance.source is PreferenceSource.STATED
    assert updated.queue_tolerance.stated_value == 0.6


def test_update_bumps_the_profile_version_and_preserves_history() -> None:
    service, _ = _service_with_guests("g1")
    service.create(factories.guest_profile(guest_id="g1", profile_version=1))

    updated = service.update(
        "g1",
        ProfileUpdate(
            queue_tolerance=PreferenceUpdate(
                value=0.9, source=PreferenceSource.LEARNED, confidence=0.6, updated_at=NOW
            )
        ),
    )

    assert updated.profile_version == 2
    assert service.get_version("g1", 1).queue_tolerance.value != 0.9
    assert service.get_version("g1", 2) == updated


def test_updating_a_guest_with_no_stored_profile_is_refused() -> None:
    service, _ = _service_with_guests("g1")

    with pytest.raises(NotFoundError):
        service.update(
            "g1",
            ProfileUpdate(
                queue_tolerance=PreferenceUpdate(
                    value=0.9, source=PreferenceSource.LEARNED, confidence=0.6, updated_at=NOW
                )
            ),
        )


def test_get_returns_none_for_an_unknown_guest() -> None:
    service, _ = _service_with_guests("g1")

    assert service.get("g1") is None


def test_updated_at_advances_on_each_update() -> None:
    service, _ = _service_with_guests("g1")
    service.create(factories.guest_profile(guest_id="g1"))

    updated = service.update(
        "g1",
        ProfileUpdate(
            walking_tolerance=PreferenceUpdate(
                value=0.2,
                source=PreferenceSource.LEARNED,
                confidence=0.6,
                updated_at=NOW + timedelta(days=1),
            )
        ),
    )

    assert updated.walking_tolerance.updated_at == NOW + timedelta(days=1)
