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

import pytest
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

from parkmind.core.contracts import (
    FairnessConfig,
    MobilityRequirement,
    PreferenceSource,
    RideRestriction,
)
from parkmind.services.personalization.group_preference_resolver import (
    GroupPreferenceResolver,
)
from parkmind.services.use_cases.resolve_group_preferences import (
    ResolveGroupPreferencesUseCase,
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
        fairness=fairness
        or FairnessConfig(lambda_fairness=0.5, min_satisfaction_floor=0.6),
    )


# ---------------------------------------------------------------------------
# Hard constraints — union, not average
# ---------------------------------------------------------------------------


def test_hard_constraints_preserve_each_guest_distinctly():
    g1, g2 = (
        guest(guest_id="g1", height_cm=170.0),
        guest(guest_id="g2", height_cm=100.0),
    )
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
    assert hard.ride_restrictions == {
        "g1": [RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE]
    }


def test_hard_constraints_pass_through_party_level_fields_unmodified():
    g1 = guest(guest_id="g1")
    constraints = party_constraints(
        guests=[g1],
        must_do=["a1", "a2"],
        avoid=["a3"],
        party_walking_budget_minutes=300,
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
    req = accessibility(
        guest_id="g1", ride_restrictions=[RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE]
    )
    thrill_ride = attraction(node_id="a1", height_restriction_cm=None)
    gentle_ride = attraction(node_id="a2", height_restriction_cm=None)

    # Only a2 has an accessibility result on file for g1; a1 has none.
    context = live_context(
        accessibility_results=[
            accessibility_check(attraction_id="a2", guest_id="g1", eligible=True)
        ]
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
    req = accessibility(
        guest_id="g1", ride_restrictions=[RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE]
    )
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
        guests=[restricted],
        accessibility_reqs=[req],
        attractions=[ride],
        context=context,
    )

    assert objective.per_guest_eligible["g1"] == []


def test_per_guest_eligible_does_not_require_accessibility_result_for_unrestricted_guest():
    unrestricted = guest(guest_id="g1", height_cm=170.0)
    ride = attraction(node_id="a1", height_restriction_cm=None)

    objective = _resolve(
        guests=[unrestricted],
        attractions=[ride],
        context=live_context(accessibility_results=[]),
    )

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
    p1 = guest_profile(
        guest_id="g1",
        queue_tolerance=preference(0.2),
        walking_tolerance=preference(0.4),
    )
    p2 = guest_profile(
        guest_id="g2",
        queue_tolerance=preference(0.8),
        walking_tolerance=preference(0.6),
    )

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

    objective = _resolve(
        guests=[guest(guest_id="g1"), guest(guest_id="g2")], profiles=[p1, p2]
    )

    assert objective.weight_provenance["queue_tolerance"] == PreferenceSource.LEARNED


def test_thematic_affinity_only_aggregated_across_guests_who_stated_it():
    p1 = guest_profile(guest_id="g1", thematic_affinity={"fantasy": preference(0.9)})
    p2 = guest_profile(guest_id="g2", thematic_affinity={})

    objective = _resolve(
        guests=[guest(guest_id="g1"), guest(guest_id="g2")], profiles=[p1, p2]
    )

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

    # Tolerance 0.1 -> 10 + 0.1 * (30 - 10) = 12 minutes. The average (0.633)
    # would give ~22.7 minutes and mask the sensitive guest.
    assert objective.event_thresholds.queue_spike_minutes == pytest.approx(12.0)


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

    p1 = guest_profile(
        guest_id="g1",
        queue_tolerance=preference(0.9),
        walking_tolerance=preference(0.9),
    )
    p2 = guest_profile(
        guest_id="g2",
        queue_tolerance=preference(0.05),
        walking_tolerance=preference(0.6),
    )
    p3 = guest_profile(
        guest_id="g3",
        queue_tolerance=preference(0.6),
        walking_tolerance=preference(0.1),
    )

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
    assert objective.event_thresholds.queue_spike_minutes == pytest.approx(11.0)
    # g3 walking tolerance 0.1 -> 5 + 0.1 * 15 = 6.5; fatigue 0.5 + 0.1 * 0.4 = 0.54.
    assert objective.event_thresholds.walking_overrun_minutes == pytest.approx(6.5)
    assert objective.event_thresholds.fatigue_threshold == pytest.approx(0.54)


# ---------------------------------------------------------------------------
# Review follow-ups: fail-closed duplicates, unprofiled guests, determinism
# ---------------------------------------------------------------------------


def test_duplicate_accessibility_results_fail_closed_regardless_of_order():
    restricted = guest(guest_id="g1", height_cm=170.0)
    req = accessibility(
        guest_id="g1", ride_restrictions=[RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE]
    )
    ride = attraction(node_id="a1", height_restriction_cm=None)
    ineligible = accessibility_check(attraction_id="a1", guest_id="g1", eligible=False)
    eligible = accessibility_check(attraction_id="a1", guest_id="g1", eligible=True)

    for results in ([ineligible, eligible], [eligible, ineligible]):
        objective = _resolve(
            guests=[restricted],
            accessibility_reqs=[req],
            attractions=[ride],
            context=live_context(accessibility_results=results),
        )
        assert objective.per_guest_eligible["g1"] == []


def test_mobility_only_guest_without_results_is_excluded():
    g1 = guest(guest_id="g1", height_cm=170.0)
    req = accessibility(
        guest_id="g1",
        ride_restrictions=[],
        mobility_requirements=[MobilityRequirement.WHEELCHAIR],
    )
    ride = attraction(node_id="a1", height_restriction_cm=None)

    objective = _resolve(guests=[g1], accessibility_reqs=[req], attractions=[ride])

    assert objective.per_guest_eligible["g1"] == []


def test_height_boundary_is_inclusive():
    coaster = attraction(node_id="a1", height_restriction_cm=112)
    exact = guest(guest_id="g1", height_cm=112.0)
    short = guest(guest_id="g2", height_cm=111.9)

    objective = _resolve(guests=[exact, short], attractions=[coaster])

    assert objective.per_guest_eligible == {"g1": ["a1"], "g2": []}


def test_guest_without_profile_contributes_default_tolerance_and_provenance():
    p1 = guest_profile(
        guest_id="g1",
        queue_tolerance=preference(
            0.9, source=PreferenceSource.STATED, stated_value=0.9
        ),
    )

    objective = _resolve(
        guests=[guest(guest_id="g1"), guest(guest_id="g2")], profiles=[p1]
    )

    assert objective.weights["queue_tolerance"] == pytest.approx((0.9 + 0.5) / 2)
    assert objective.weight_provenance["queue_tolerance"] == PreferenceSource.DEFAULT


def test_guest_without_profile_can_drive_event_thresholds():
    tolerant = guest_profile(
        guest_id="g1",
        queue_tolerance=preference(0.9),
        walking_tolerance=preference(0.9),
    )

    objective = _resolve(
        guests=[guest(guest_id="g1"), guest(guest_id="g2")], profiles=[tolerant]
    )

    # g2 has no profile: default queue tolerance 0.5 -> 20 minutes, not 0.9 -> 28.
    assert objective.event_thresholds.queue_spike_minutes == pytest.approx(20.0)


@pytest.mark.parametrize(
    ("tolerance", "queue", "walking", "fatigue"),
    [(0.0, 10.0, 5.0, 0.5), (0.5, 20.0, 12.5, 0.7), (1.0, 30.0, 20.0, 0.9)],
)
def test_event_threshold_scale_endpoints(tolerance, queue, walking, fatigue):
    profile = guest_profile(
        guest_id="g1",
        queue_tolerance=preference(tolerance),
        walking_tolerance=preference(tolerance),
    )

    thresholds = _resolve(
        guests=[guest(guest_id="g1")], profiles=[profile]
    ).event_thresholds

    assert thresholds.queue_spike_minutes == pytest.approx(queue)
    assert thresholds.walking_overrun_minutes == pytest.approx(walking)
    assert thresholds.fatigue_threshold == pytest.approx(fatigue)


def test_resolution_is_independent_of_input_order():
    g1, g2 = guest(guest_id="g1"), guest(guest_id="g2")
    p1 = guest_profile(
        guest_id="g1",
        queue_tolerance=preference(0.3),
        thematic_affinity={"a": preference(0.1), "b": preference(0.7)},
    )
    p2 = guest_profile(
        guest_id="g2",
        queue_tolerance=preference(0.7),
        thematic_affinity={"b": preference(0.2), "a": preference(0.9)},
    )

    forward = _resolve(guests=[g1, g2], profiles=[p1, p2])
    backward = _resolve(guests=[g2, g1], profiles=[p2, p1])

    assert forward == backward


def test_resolver_does_not_mutate_or_alias_party_constraints():
    g1 = guest(guest_id="g1")
    constraints = party_constraints(guests=[g1], must_do=["a1"])

    objective = _resolve(guests=[g1], constraints=constraints)
    objective.hard_constraints.must_do.append("zz")

    assert constraints.must_do == ["a1"]


def test_use_case_delegates_to_resolver():
    g1 = guest(guest_id="g1", height_cm=170.0)
    ride = attraction(node_id="a1", height_restriction_cm=None)
    kwargs = {
        "guests": [g1],
        "profiles": [],
        "accessibility": [],
        "attractions": [ride],
        "party_constraints": party_constraints(guests=[g1]),
        "live_context": live_context(),
        "fairness": FairnessConfig(lambda_fairness=1.0, min_satisfaction_floor=0.2),
    }

    assert ResolveGroupPreferencesUseCase().execute(**kwargs) == RESOLVER.resolve(
        **kwargs
    )


def test_profile_of_guest_outside_the_party_is_ignored():
    inside = guest_profile(guest_id="g1", queue_tolerance=preference(0.8))
    outsider = guest_profile(guest_id="gX", queue_tolerance=preference(0.0))

    objective = _resolve(guests=[guest(guest_id="g1")], profiles=[inside, outsider])

    assert objective.weights["queue_tolerance"] == pytest.approx(0.8)
    assert objective.event_thresholds.queue_spike_minutes == pytest.approx(26.0)


def test_duplicate_profile_for_same_guest_counts_once():
    first = guest_profile(guest_id="g1", queue_tolerance=preference(0.2))
    repeat = guest_profile(guest_id="g1", queue_tolerance=preference(0.2))
    other = guest_profile(guest_id="g2", queue_tolerance=preference(0.8))

    objective = _resolve(
        guests=[guest(guest_id="g1"), guest(guest_id="g2")],
        profiles=[first, repeat, other],
    )

    assert objective.weights["queue_tolerance"] == pytest.approx(0.5)


def test_thresholds_for_all_default_group_do_not_depend_on_profile_presence():
    no_profiles = _resolve(guests=[guest(guest_id="g1")], profiles=[])
    empty_party = _resolve(
        guests=[],
        profiles=[],
        constraints=party_constraints(guests=[guest(guest_id="g1")]),
    )

    # Default tolerances 0.5 / 0.7 on the shared scale.
    for objective in (no_profiles, empty_party):
        assert objective.event_thresholds.queue_spike_minutes == pytest.approx(20.0)
        assert objective.event_thresholds.walking_overrun_minutes == pytest.approx(15.5)
        assert objective.event_thresholds.fatigue_threshold == pytest.approx(0.78)


def test_output_is_byte_identical_regardless_of_guest_and_profile_order():
    g1, g2 = (
        guest(guest_id="g1", height_cm=170.0),
        guest(guest_id="g2", height_cm=120.0),
    )
    p1 = guest_profile(guest_id="g1", queue_tolerance=preference(0.3))
    p2 = guest_profile(guest_id="g2", queue_tolerance=preference(0.7))
    reqs = [
        accessibility(guest_id="g2", daily_walking_limit_minutes=60),
        accessibility(guest_id="g1", daily_walking_limit_minutes=90),
    ]

    forward = _resolve(guests=[g1, g2], profiles=[p1, p2], accessibility_reqs=reqs)
    backward = _resolve(
        guests=[g2, g1], profiles=[p2, p1], accessibility_reqs=reqs[::-1]
    )

    assert forward.model_dump_json() == backward.model_dump_json()
