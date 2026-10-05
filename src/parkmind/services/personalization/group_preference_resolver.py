"""
GroupPreferenceResolver — combines per-guest GuestProfiles, hard constraints,
and live accessibility results into one GroupObjective.

Deterministic core: no I/O, no LLM (§11, [C12, C20, C23]). Input to
PreferenceScorer and Optimizer.

Rules:
- Hard constraints are a union, never an average: every guest's
  accessibility-derived limits (walking, rest, height, ride restrictions)
  are preserved per guest_id, so one guest's constraint can never be
  diluted or dropped by the rest of the party.
- `per_guest_eligible` is computed per guest from two catalog-time rules
  only: rule 2 (HEIGHT, from Attraction.height_restriction_cm) and rule 10
  (RIDE_RESTRICTION, from LiveContext.accessibility_results). Both fail
  closed: an unknown guest height or a missing accessibility result for a
  restricted guest excludes the attraction, mirroring
  ConstraintChecker._check_height / _check_ride_restriction. Rule 9
  (ACCESSIBILITY: walking limits, rest frequency, mobility) is plan-time
  (depends on stop scheduling) and is intentionally out of scope here.
  Duplicate accessibility results for the same (attraction, guest) are
  resolved fail-closed: any ineligible result wins.
- Soft preferences (queue/walking tolerance, thematic affinities) are
  aggregated as a deterministic mean. A guest with no profile contributes
  the documented default tolerance with DEFAULT provenance, so they are
  never silently absent from the group figure.
  `weight_provenance` records the weakest evidence among contributors
  (DEFAULT < LEARNED < STATED), so a group figure is never reported as
  "stated" when any contributor's value was only a default.
- Event thresholds are derived from the single most-sensitive guest (the
  guest with the lowest tolerance, including default tolerance for guests
  without a profile), never the group average: the same 35-minute queue
  spike is a MEDIUM event for a low-tolerance guest and shouldn't be masked
  by a high-tolerance party member.
"""

from collections.abc import Callable

from parkmind.core.contracts import (
    AccessibilityRequirements,
    Attraction,
    EventThresholds,
    FairnessConfig,
    GroupObjective,
    Guest,
    GuestProfile,
    HardConstraintSet,
    LiveContext,
    PartyConstraints,
    PreferenceSource,
    PreferenceValue,
)

_DEFAULT_QUEUE_TOLERANCE = 0.5
_DEFAULT_WALKING_TOLERANCE = 0.7

# (value_at_tolerance_0, value_at_tolerance_1) — the most sensitive guest
# (lowest tolerance) drives the low end of each range.
_QUEUE_SPIKE_MINUTES_RANGE = (10.0, 30.0)
_WALKING_OVERRUN_MINUTES_RANGE = (5.0, 20.0)
_FATIGUE_THRESHOLD_RANGE = (0.5, 0.9)

_SOURCE_PRECEDENCE = {
    PreferenceSource.DEFAULT: 0,
    PreferenceSource.LEARNED: 1,
    PreferenceSource.STATED: 2,
}


def _scale(tolerance: float, low: float, high: float) -> float:
    return low + tolerance * (high - low)


def _aggregate_dimension(
    entries: list[tuple[float, PreferenceSource]],
) -> tuple[float, PreferenceSource] | None:
    """Mean value + weakest-evidence provenance across the given entries."""
    if not entries:
        return None
    mean_value = sum(value for value, _ in entries) / len(entries)
    weakest_source = min(
        (source for _, source in entries), key=lambda s: _SOURCE_PRECEDENCE[s]
    )
    return mean_value, weakest_source


def _entries(
    profiles: list[GuestProfile],
    getter: Callable[[GuestProfile], PreferenceValue | None],
) -> list[tuple[float, PreferenceSource]]:
    entries: list[tuple[float, PreferenceSource]] = []
    for profile in profiles:
        pv = getter(profile)
        if pv is not None:
            entries.append((pv.value, pv.source))
    return entries


def _aggregate_weights(
    profiles: list[GuestProfile],
    unprofiled_guest_count: int = 0,
) -> tuple[dict[str, float], dict[str, PreferenceSource]]:
    weights: dict[str, float] = {}
    weight_provenance: dict[str, PreferenceSource] = {}

    for key, getter, default in (
        ("queue_tolerance", lambda p: p.queue_tolerance, _DEFAULT_QUEUE_TOLERANCE),
        (
            "walking_tolerance",
            lambda p: p.walking_tolerance,
            _DEFAULT_WALKING_TOLERANCE,
        ),
    ):
        entries = _entries(profiles, getter)
        entries += [(default, PreferenceSource.DEFAULT)] * unprofiled_guest_count
        aggregated = _aggregate_dimension(entries)
        if aggregated is None:
            weights[key], weight_provenance[key] = default, PreferenceSource.DEFAULT
        else:
            weights[key], weight_provenance[key] = aggregated

    themes = sorted(
        {theme for profile in profiles for theme in profile.thematic_affinity}
    )
    for theme in themes:
        theme_entries = [
            (
                profile.thematic_affinity[theme].value,
                profile.thematic_affinity[theme].source,
            )
            for profile in profiles
            if theme in profile.thematic_affinity
        ]
        aggregated = _aggregate_dimension(theme_entries)
        if aggregated is not None:
            key = f"affinity:{theme}"
            weights[key], weight_provenance[key] = aggregated

    return weights, weight_provenance


def _derive_event_thresholds(
    profiles: list[GuestProfile],
    unprofiled_guest_count: int = 0,
) -> EventThresholds:
    """Most-sensitive-guest derivation: driven by the lowest tolerance, not the average."""
    if not profiles:
        return EventThresholds()
    queue_tolerances = [p.queue_tolerance.value for p in profiles]
    walking_tolerances = [p.walking_tolerance.value for p in profiles]
    if unprofiled_guest_count:
        queue_tolerances.append(_DEFAULT_QUEUE_TOLERANCE)
        walking_tolerances.append(_DEFAULT_WALKING_TOLERANCE)
    min_queue_tolerance = min(queue_tolerances)
    min_walking_tolerance = min(walking_tolerances)
    return EventThresholds(
        queue_spike_minutes=_scale(min_queue_tolerance, *_QUEUE_SPIKE_MINUTES_RANGE),
        walking_overrun_minutes=_scale(
            min_walking_tolerance, *_WALKING_OVERRUN_MINUTES_RANGE
        ),
        fatigue_threshold=_scale(min_walking_tolerance, *_FATIGUE_THRESHOLD_RANGE),
    )


def _union_hard_constraints(
    guests: list[Guest],
    accessibility: list[AccessibilityRequirements],
    party_constraints: PartyConstraints,
) -> HardConstraintSet:
    accessibility_by_guest = {req.guest_id: req for req in accessibility}

    height_constraints = {
        guest.guest_id: guest.height_cm
        for guest in guests
        if guest.height_cm is not None
    }
    per_guest_daily_walking_limits = {
        guest_id: req.daily_walking_limit_minutes
        for guest_id, req in accessibility_by_guest.items()
        if req.daily_walking_limit_minutes is not None
    }
    per_guest_rest_frequency = {
        guest_id: req.rest_frequency_minutes
        for guest_id, req in accessibility_by_guest.items()
        if req.rest_frequency_minutes is not None
    }
    ride_restrictions = {
        guest_id: [restriction.value for restriction in req.ride_restrictions]
        for guest_id, req in accessibility_by_guest.items()
        if req.ride_restrictions
    }

    return HardConstraintSet(
        must_do=list(party_constraints.must_do),
        avoid=list(party_constraints.avoid),
        party_walking_budget_minutes=party_constraints.party_walking_budget_minutes,
        per_guest_daily_walking_limits=per_guest_daily_walking_limits,
        per_guest_rest_frequency=per_guest_rest_frequency,
        height_constraints=height_constraints,
        ride_restrictions=ride_restrictions,
    )


def _per_guest_eligible(
    guests: list[Guest],
    accessibility: list[AccessibilityRequirements],
    attractions: list[Attraction],
    live_context: LiveContext,
) -> dict[str, list[str]]:
    accessibility_by_guest = {req.guest_id: req for req in accessibility}
    # Fail closed on duplicates: any ineligible result for a pair wins.
    check_eligible: dict[tuple[str, str], bool] = {}
    for check in live_context.accessibility_results:
        key = (check.attraction_id, check.guest_id)
        check_eligible[key] = check_eligible.get(key, True) and check.eligible

    eligible: dict[str, list[str]] = {}
    for guest in guests:
        req = accessibility_by_guest.get(guest.guest_id)
        is_restricted = req is not None and (
            req.ride_restrictions or req.mobility_requirements
        )

        guest_eligible: list[str] = []
        for attraction in attractions:
            # Rule 2 (HEIGHT): fails closed on unknown guest height.
            if attraction.height_restriction_cm is not None and (
                guest.height_cm is None
                or guest.height_cm < attraction.height_restriction_cm
            ):
                continue

            # Rule 10 (RIDE_RESTRICTION): only applies to guests with a
            # declared restriction; fails closed on a missing result.
            if is_restricted and not check_eligible.get(
                (attraction.node_id, guest.guest_id), False
            ):
                continue

            guest_eligible.append(attraction.node_id)

        eligible[guest.guest_id] = guest_eligible

    return eligible


class GroupPreferenceResolver:
    """Combines per-guest profiles and hard constraints into one GroupObjective."""

    def resolve(
        self,
        *,
        guests: list[Guest],
        profiles: list[GuestProfile],
        accessibility: list[AccessibilityRequirements],
        attractions: list[Attraction],
        party_constraints: PartyConstraints,
        live_context: LiveContext,
        fairness: FairnessConfig,
        objective_version: str = "1",
    ) -> GroupObjective:
        profiled_ids = {profile.guest_id for profile in profiles}
        unprofiled = sum(1 for g in guests if g.guest_id not in profiled_ids)
        weights, weight_provenance = _aggregate_weights(profiles, unprofiled)
        return GroupObjective(
            objective_version=objective_version,
            weights=weights,
            per_guest_eligible=_per_guest_eligible(
                guests, accessibility, attractions, live_context
            ),
            hard_constraints=_union_hard_constraints(
                guests, accessibility, party_constraints
            ),
            fairness=fairness,
            event_thresholds=_derive_event_thresholds(profiles, unprofiled),
            weight_provenance=weight_provenance,
        )
