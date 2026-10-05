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
  ConstraintChecker._check_height / _check_ride_restriction. Rule 9 (heat
  sensitivity) is plan-time (depends on stop scheduling against weather)
  and is intentionally out of scope here.
- Soft preferences (queue/walking tolerance, thematic affinities) are
  aggregated as a deterministic mean across guests who stated an opinion.
  `weight_provenance` records the weakest evidence among contributors
  (DEFAULT < LEARNED < STATED), so a group figure is never reported as
  "stated" when any contributor's value was only a default.
- Event thresholds are derived from the single most-sensitive guest (the
  guest with the lowest tolerance), never the group average: the same
  35-minute queue spike is a MEDIUM event for a low-tolerance guest and
  shouldn't be masked by a high-tolerance party member.
"""

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
    profiles: list[GuestProfile],
    getter,
) -> tuple[float, PreferenceSource] | None:
    """Mean value + weakest-evidence provenance across profiles that set this dimension."""
    values: list[float] = []
    sources: list[PreferenceSource] = []
    for profile in profiles:
        pv: PreferenceValue | None = getter(profile)
        if pv is None:
            continue
        values.append(pv.value)
        sources.append(pv.source)
    if not values:
        return None
    mean_value = sum(values) / len(values)
    weakest_source = min(sources, key=lambda s: _SOURCE_PRECEDENCE[s])
    return mean_value, weakest_source


def _aggregate_weights(
    profiles: list[GuestProfile],
) -> tuple[dict[str, float], dict[str, PreferenceSource]]:
    weights: dict[str, float] = {}
    weight_provenance: dict[str, PreferenceSource] = {}

    queue = _aggregate_dimension(profiles, lambda p: p.queue_tolerance)
    if queue is None:
        weights["queue_tolerance"] = _DEFAULT_QUEUE_TOLERANCE
        weight_provenance["queue_tolerance"] = PreferenceSource.DEFAULT
    else:
        weights["queue_tolerance"], weight_provenance["queue_tolerance"] = queue

    walking = _aggregate_dimension(profiles, lambda p: p.walking_tolerance)
    if walking is None:
        weights["walking_tolerance"] = _DEFAULT_WALKING_TOLERANCE
        weight_provenance["walking_tolerance"] = PreferenceSource.DEFAULT
    else:
        weights["walking_tolerance"], weight_provenance["walking_tolerance"] = walking

    themes = sorted({theme for profile in profiles for theme in profile.thematic_affinity})
    for theme in themes:
        aggregated = _aggregate_dimension(
            profiles, lambda p, t=theme: p.thematic_affinity.get(t)
        )
        if aggregated is not None:
            key = f"affinity:{theme}"
            weights[key], weight_provenance[key] = aggregated

    return weights, weight_provenance


def _derive_event_thresholds(profiles: list[GuestProfile]) -> EventThresholds:
    """Most-sensitive-guest derivation: driven by the lowest tolerance, not the average."""
    if not profiles:
        return EventThresholds()
    min_queue_tolerance = min(p.queue_tolerance.value for p in profiles)
    min_walking_tolerance = min(p.walking_tolerance.value for p in profiles)
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
        guest.guest_id: guest.height_cm for guest in guests if guest.height_cm is not None
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
        guest_id: list(req.ride_restrictions)
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
    checks_by_key = {
        (check.attraction_id, check.guest_id): check
        for check in live_context.accessibility_results
    }

    eligible: dict[str, list[str]] = {}
    for guest in guests:
        req = accessibility_by_guest.get(guest.guest_id)
        is_restricted = req is not None and (req.ride_restrictions or req.mobility_requirements)

        guest_eligible: list[str] = []
        for attraction in attractions:
            # Rule 2 (HEIGHT): fails closed on unknown guest height.
            if attraction.height_restriction_cm is not None and (
                guest.height_cm is None or guest.height_cm < attraction.height_restriction_cm
            ):
                continue

            # Rule 10 (RIDE_RESTRICTION): only applies to guests with a
            # declared restriction; fails closed on a missing result.
            if is_restricted:
                check = checks_by_key.get((attraction.node_id, guest.guest_id))
                if check is None or not check.eligible:
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
        weights, weight_provenance = _aggregate_weights(profiles)
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
            event_thresholds=_derive_event_thresholds(profiles),
            weight_provenance=weight_provenance,
        )
