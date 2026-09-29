"""
GuestProfileService — storage, retrieval and validated updates of per-guest
`GuestProfile` aggregates.

`ProfileRepository` only appends immutable versions; it does not know which
dimensions a caller meant to change. This service owns that merge: a partial
`ProfileUpdate` is applied onto the latest stored profile so unrelated
dimensions -- and, on every `PreferenceValue`, `stated_value` -- survive.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from parkmind.core.contracts import (
    AttractionCategory,
    GuestProfile,
    PlanningPace,
    PlanningStyle,
    PreferenceSource,
    PreferenceValue,
    SensitivityKind,
    SensitivityLevel,
)
from parkmind.services.ports import NotFoundError, ProfileRepository


@dataclass(frozen=True)
class PreferenceUpdate:
    """A new observation for one `PreferenceValue` dimension.

    `stated_value` is only meaningful when `source` is STATED, where it must
    equal `value` if given at all. For a LEARNED/DEFAULT update the profile's
    current `stated_value` is carried forward untouched, so `stated_value`
    must be left `None`. A DEFAULT update is only accepted when the
    dimension has no prior stated or learned value.
    """

    value: float
    source: PreferenceSource
    confidence: float
    updated_at: datetime
    stated_value: float | None = None


@dataclass(frozen=True)
class ProfileUpdate:
    """Partial change to a `GuestProfile`.

    A field left as `None` leaves that dimension untouched. `sensitivities`
    and `thematic_affinity` are merged key-wise, so an update naming one
    sensitivity or theme does not erase the others -- there is no way to
    remove a sensitivity or theme through this update.

    `sensitivities={}` is therefore a no-op (nothing to merge in), while
    `preferred_categories=[]` / `avoided_categories=[]` do replace the
    current list with an empty one, since those fields are replaced
    wholesale rather than merged. A `ProfileUpdate()` with every field left
    `None` is also a no-op: `update()` returns the current profile without
    persisting a new version.
    """

    pace: PlanningPace | None = None
    planning_style: PlanningStyle | None = None
    queue_tolerance: PreferenceUpdate | None = None
    walking_tolerance: PreferenceUpdate | None = None
    sensitivities: dict[SensitivityKind, SensitivityLevel] | None = None
    thematic_affinity: dict[str, PreferenceUpdate] | None = None
    preferred_categories: list[AttractionCategory] | None = None
    avoided_categories: list[AttractionCategory] | None = None


def _merge_preference(
    current: PreferenceValue | None, update: PreferenceUpdate
) -> PreferenceValue:
    stated_value: float | None
    if update.source == PreferenceSource.STATED:
        if update.stated_value is not None and update.stated_value != update.value:
            raise ValueError(
                "a STATED update's stated_value must equal value "
                f"(got value={update.value!r}, stated_value={update.stated_value!r})"
            )
        stated_value = update.value
    else:
        if update.stated_value is not None:
            raise ValueError(
                f"a {update.source.value.upper()} update must not set stated_value"
            )
        if update.source == PreferenceSource.DEFAULT and current is not None:
            has_prior_value = current.stated_value is not None or current.source != (
                PreferenceSource.DEFAULT
            )
            if has_prior_value:
                raise ValueError(
                    "a DEFAULT update cannot overwrite a dimension that already has a "
                    "stated or learned value"
                )
        stated_value = current.stated_value if current is not None else None
    return PreferenceValue(
        value=update.value,
        source=update.source,
        confidence=update.confidence,
        updated_at=update.updated_at,
        stated_value=stated_value,
    )


class GuestProfileService:
    """Storage, retrieval and validated updates of per-guest `GuestProfile`s."""

    def __init__(self, profiles: ProfileRepository) -> None:
        self._profiles = profiles

    def create(self, profile: GuestProfile) -> GuestProfile:
        """Persist `profile` as a new version (typically the first one)."""
        self._profiles.save(profile)
        return profile

    def get(self, guest_id: str) -> GuestProfile | None:
        """The latest stored profile for `guest_id`, or `None`."""
        return self._profiles.get_latest(guest_id)

    def get_version(self, guest_id: str, profile_version: int) -> GuestProfile | None:
        return self._profiles.get_version(guest_id, profile_version)

    def update(self, guest_id: str, update: ProfileUpdate) -> GuestProfile:
        """Apply `update` onto the latest stored profile and persist a new version.

        Raises `NotFoundError` if the guest has no stored profile yet. Raises
        `ProfileVersionConflictError` (via the repository) if another update
        was persisted concurrently -- nothing is applied; the caller must
        retry against the latest version. A `ProfileUpdate` with every field
        `None` returns the current profile unchanged, without persisting a
        new version.
        """
        current = self._profiles.get_latest(guest_id)
        if current is None:
            raise NotFoundError(f"no stored profile for guest {guest_id!r}")

        if update.sensitivities == {}:
            update = replace(update, sensitivities=None)
        if update.thematic_affinity == {}:
            update = replace(update, thematic_affinity=None)

        if update == ProfileUpdate():
            return current

        sensitivities = dict(current.sensitivities)
        if update.sensitivities:
            sensitivities.update(update.sensitivities)

        thematic_affinity = dict(current.thematic_affinity)
        if update.thematic_affinity:
            for theme, theme_update in update.thematic_affinity.items():
                thematic_affinity[theme] = _merge_preference(
                    thematic_affinity.get(theme), theme_update
                )

        updated = GuestProfile(
            guest_id=current.guest_id,
            pace=current.pace if update.pace is None else update.pace,
            queue_tolerance=(
                current.queue_tolerance
                if update.queue_tolerance is None
                else _merge_preference(current.queue_tolerance, update.queue_tolerance)
            ),
            walking_tolerance=(
                current.walking_tolerance
                if update.walking_tolerance is None
                else _merge_preference(current.walking_tolerance, update.walking_tolerance)
            ),
            sensitivities=sensitivities,
            thematic_affinity=thematic_affinity,
            preferred_categories=(
                current.preferred_categories
                if update.preferred_categories is None
                else update.preferred_categories
            ),
            avoided_categories=(
                current.avoided_categories
                if update.avoided_categories is None
                else update.avoided_categories
            ),
            planning_style=(
                current.planning_style if update.planning_style is None else update.planning_style
            ),
            profile_version=current.profile_version + 1,
        )
        self._profiles.save(updated)
        return updated
