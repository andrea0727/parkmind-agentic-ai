"""Tests for GroupPreferenceResolver (P0-16).

Covers every "done when" bullet from the ticket:
- individual hard constraints preserved as a union
- per_guest_eligible excludes attractions with no accessibility result for a
  restricted guest
- soft preferences aggregated deterministically, with weight_provenance
  recording stated/learned/default per weight
- fairness parameters are explicit and pass through untouched
- event thresholds equal the most sensitive guest's tolerance, not the
  average
- conflicting personas: no guest's constraints/eligibility/preferences are
  silently erased
"""

from factories import (
    accessibility,
    accessibility_check,
    attraction,
    guest,
    guest_profile,
    live_context,
    party_constraints,
    preference,
)

from parkmind.core.contracts import FairnessConfig, MobilityRequirement, PreferenceSource, RideRestriction
from parkmind.services.personalization.group_preference_resolver import (
    GroupPreferenceResolver,
    _scale,
    _QUEUE_SPIKE_MINUTES_RANGE,
)

RESOLVER = GroupPreferenceResolver()


def _resolve(
    *,
    guests,
    profiles=(),
    accessibility_reqs=(),
    attractions=(),
    constraints=None,
    context=None,
    fairness=None,
):
    return RESOLVER.resolve(
        guests=list(guests),
        profiles=list(profiles),
        accessibility=list(accessibility_reqs),
        attractions=list(attractions),
        party_constraints=constraints or party_constraints(guests=list(guests)),
        live_context=context or live_context(),
        fairness=fairness or FairnessConfig(lambda_fairness=0.5, min_satisfaction_floor=0.6),
    )


# ---------------------------------------------------------------------------
# Hard constraints — union, not average
# ---------------------------------------------------------------------------


def test_hard_constraints_preserve_each_guest_distinctly():
    g1, g2 = guest(guest_id="g1", height_cm=170.0), guest(guest_id="g2", height_cm=100.0)
    a1 = accessibility(
        guest_id="g1",
        daily_walking_limit_minutes=120,
        rest_frequency_minutes=90,
        ride_restrictions=[RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE],
        mobility_requirements=[],
    )
    a2 = accessibility(
        guest_id="g2",
        daily_walking_limit_minutes=45,
        rest_frequency_minutes=30,
        ride_restrictions=[],
        mobility_requirements=[MobilityRequirement.WHEELCHAIR],
    )

    objective = _resolve(guests=[g1, g2], accessibility_reqs=[a1, a2])
    hard = objective.hard_constraints

    assert hard.per_guest_daily_walking_limits == {"g1": 120, "g2": 45}
    assert hard.per_guest_rest_frequency == {"g1": 90, "g2": 30}
    assert hard.height_constraints == {"g1": 170.0, "g2": 100.0}
    assert hard.ride_restrictions == {"g1": [RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE]}


def test_hard_constraints_pass_through_party_level_fields_unmodified():
    g1 = guest(guest_id="g1")
    constraints = party_constraints(
        guests=[g1], must_do=["a1", "a2"], avoid=["a3"], party_walking_budget_minutes=300
    )

    objective = _resolve(guests=[g1], constraints=constraints)

    assert objective.hard_constraints.must_do == ["a1", "a2"]
    assert objective.hard_constraints.avoid == ["a3"]
    assert objective.hard_constraints.party_walking_budget_minutes == 300


# ---------------------------------------------------------------------------
# per_guest_eligible — rule 2 (HEIGHT) + rule 10 (RIDE_RESTRICTION), fail closed
# ---------------------------------------------------------------------------


def test_per_guest_eligible_excludes_attraction_missing_accessibility_result_for_restricted_guest():
    restricted = guest(guest_id="g1", height_cm=170.0)
    req = accessibility(guest_id="g1", ride_restrictions=[RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE])
    thrill_ride = attraction(node_id="a1", height_restriction_cm=None)
    gentle_ride = attraction(node_id="a2", height_restriction_cm=None)

    # Only a2 has an accessibility result on file for g1; a1 has none.
    context = live_context(
        accessibility_results=[accessibility_check(attraction_id="a2", guest_id="g1", eligible=True)]
    )

    objective = _resolve(
        guests=[restricted],
        accessibility_reqs=[req],
        attractions=[thrill_ride, gentle_ride],
        context=context,
    )

    assert objective.per_guest_eligible["g1"] == ["a2"]


def test_per_guest_eligible_excludes_attraction_when_accessibility_result_is_not_eligible():
    restricted = guest(guest_id="g1", height_cm=170.0)
    req = accessibility(guest_id="g1", ride_restrictions=[RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE])
    ride = attraction(node_id="a1", height_restriction_cm=None)
    context = live_context(
        accessibility_results=[
            accessibility_check(
                attraction_id="a1",
                guest_id="g1",
                eligible=False,
                conflicting_requirement="NOT_RECOMMENDED_HIGH_G_FORCE",
            )
        ]
    )

    objective = _resolve(
        guests=[restricted], accessibility_reqs=[req], attractions=[ride], context=context
    )

    assert objective.per_guest_eligible["g1"] == []


def test_per_guest_eligible_does_not_require_accessibility_result_for_unrestricted_guest():
    unrestricted = guest(guest_id="g1", height_cm=170.0)
    ride = attraction(node_id="a1", height_restriction_cm=None)

    objective = _resolve(guests=[unrestricted], attractions=[ride], context=live_context(accessibility_results=[]))

    assert objective.per_guest_eligible["g1"] == ["a1"]


def test_per_guest_eligible_height_rule_fails_closed_on_unknown_guest_height():
    unknown_height = guest(guest_id="g1", height_cm=None)
    tall_enough = guest(guest_id="g2", height_cm=150.0)
    coaster = attraction(node_id="a1", height_restriction_cm=112)

    objective = _resolve(guests=[unknown_height, tall_enough], attractions=[coaster])

    assert objective.per_guest_eligible["g1"] == []
    assert objective.per_guest_eligible["g2"] == ["a1"]


# ---------------------------------------------------------------------------
# Soft preference aggregation + weight_provenance
# ---------------------------------------------------------------------------


def test_soft_preferences_aggregate_as_deterministic_mean():
    p1 = guest_profile(guest_id="g1", queue_tolerance=preference(0.2), walking_tolerance=preference(0.4))
    p2 = guest_profile(guest_id="g2", queue_tolerance=preference(0.8), walking_tolerance=preference(0.6))

    objective = _resolve(
        guests=[guest(guest_id="g1"), guest(guest_id="g2")], profiles=[p1, p2]
    )

    assert objective.weights["queue_tolerance"] == 0.5
    assert objective.weights["walking_tolerance"] == 0.5


def test_weight_provenance_records_weakest_contributing_source():
    stated = preference(0.2, source=PreferenceSource.STATED, stated_value=0.2)
    learned = preference(0.8, source=PreferenceSource.LEARNED, stated_value=None)

    p1 = guest_profile(guest_id="g1", queue_tolerance=stated)
    p2 = guest_profile(guest_id="g2", queue_tolerance=learned)

    objective = _resolve(guests=[guest(guest_id="g1"), guest(guest_id="g2")], profiles=[p1, p2])

    assert objective.weight_provenance["queue_tolerance"] == PreferenceSource.LEARNED


def test_thematic_affinity_only_aggregated_across_guests_who_stated_it():
    p1 = guest_profile(guest_id="g1", thematic_affinity={"fantasy": preference(0.9)})
    p2 = guest_profile(guest_id="g2", thematic_affinity={})

    objective = _resolve(guests=[guest(guest_id="g1"), guest(guest_id="g2")], profiles=[p1, p2])

    assert objective.weights["affinity:fantasy"] == 0.9


def test_soft_preferences_fall_back_to_documented_defaults_when_no_profiles():
    objective = _resolve(guests=[guest(guest_id="g1")], profiles=[])

    assert objective.weights["queue_tolerance"] == 0.5
    assert objective.weights["walking_tolerance"] == 0.7
    assert objective.weight_provenance["queue_tolerance"] == PreferenceSource.DEFAULT
    assert objective.weight_provenance["walking_tolerance"] == PreferenceSource.DEFAULT


# ---------------------------------------------------------------------------
# Fairness — explicit, testable, passed through unchanged
# ---------------------------------------------------------------------------


def test_fairness_config_passes_through_unchanged():
    fairness = FairnessConfig(lambda_fairness=2.5, min_satisfaction_floor=0.35)

    objective = _resolve(guests=[guest(guest_id="g1")], fairness=fairness)

    assert objective.fairness == fairness


# ---------------------------------------------------------------------------
# Event thresholds — most sensitive guest, not the average
# ---------------------------------------------------------------------------


def test_event_thresholds_driven_by_most_sensitive_guest_not_average():
    tolerant_1 = guest_profile(guest_id="g1", queue_tolerance=preference(0.9))
    tolerant_2 = guest_profile(guest_id="g2", queue_tolerance=preference(0.9))
    sensitive = guest_profile(guest_id="g3", queue_tolerance=preference(0.1))

    objective = _resolve(
        guests=[guest(guest_id=g) for g in ("g1", "g2", "g3")],
        profiles=[tolerant_1, tolerant_2, sensitive],
    )

    expected_from_min = _scale(0.1, *_QUEUE_SPIKE_MINUTES_RANGE)
    average_tolerance = (0.9 + 0.9 + 0.1) / 3
    expected_from_average = _scale(average_tolerance, *_QUEUE_SPIKE_MINUTES_RANGE)

    assert objective.event_thresholds.queue_spike_minutes == expected_from_min
    assert objective.event_thresholds.queue_spike_minutes != expected_from_average


# ---------------------------------------------------------------------------
# Conflicting personas — no guest silently erased
# ---------------------------------------------------------------------------


def test_conflicting_personas_are_not_silently_erased():
    # g1: thrill-seeking parent, high tolerance for everything.
    # g2: anxious guest, very low queue tolerance, no accessibility needs.
    # g3: mobility-restricted grandparent, low walking tolerance.
    g1 = guest(guest_id="g1", height_cm=180.0)
    g2 = guest(guest_id="g2", height_cm=170.0)
    g3 = guest(guest_id="g3", height_cm=160.0)

    p1 = guest_profile(guest_id="g1", queue_tolerance=preference(0.9), walking_tolerance=preference(0.9))
    p2 = guest_profile(guest_id="g2", queue_tolerance=preference(0.05), walking_tolerance=preference(0.6))
    p3 = guest_profile(guest_id="g3", queue_tolerance=preference(0.6), walking_tolerance=preference(0.1))

    req3 = accessibility(
        guest_id="g3",
        daily_walking_limit_minutes=40,
        rest_frequency_minutes=45,
        ride_restrictions=[RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE],
        mobility_requirements=[MobilityRequirement.WHEELCHAIR],
    )

    coaster = attraction(node_id="coaster", height_restriction_cm=None)
    context = live_context(
        accessibility_results=[
            accessibility_check(attraction_id="coaster", guest_id="g3", eligible=False)
        ]
    )

    objective = _resolve(
        guests=[g1, g2, g3],
        profiles=[p1, p2, p3],
        accessibility_reqs=[req3],
        attractions=[coaster],
        context=context,
    )

    # No guest's hard constraints dropped.
    assert objective.hard_constraints.per_guest_daily_walking_limits == {"g3": 40}
    assert objective.hard_constraints.per_guest_rest_frequency == {"g3": 45}
    assert objective.hard_constraints.ride_restrictions == {
        "g3": [RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE]
    }

    # Every guest still has their own eligibility entry: g3 excluded from the
    # coaster (fails closed / ineligible), g1 and g2 unaffected by g3's
    # restriction.
    assert objective.per_guest_eligible["g1"] == ["coaster"]
    assert objective.per_guest_eligible["g2"] == ["coaster"]
    assert objective.per_guest_eligible["g3"] == []

    # Aggregated weight reflects all three guests, not just the loudest one:
    # g2's very low queue tolerance pulls it down but doesn't erase g1/g3.
    assert 0.05 < objective.weights["queue_tolerance"] < 0.9

    # Event thresholds protect the single most sensitive guest (g2's queue
    # tolerance, g3's walking tolerance), not an averaged-away compromise.
    expected_queue_threshold = _scale(0.05, *_QUEUE_SPIKE_MINUTES_RANGE)
    assert objective.event_thresholds.queue_spike_minutes == expected_queue_threshold
