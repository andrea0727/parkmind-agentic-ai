"""Tests for ConstraintChecker — the functional safety gate (P0-15)."""

from datetime import timedelta

from factories import (
    NOW,
    accessibility,
    accessibility_check,
    attraction,
    guest,
    live_context,
    park,
    party_constraints,
    plan,
    stop,
)

from parkmind.core.contracts import (
    AttractionStatus,
    CoverageReport,
    RuleId,
    StopKind,
    TimeWindow,
    WeatherHour,
)
from parkmind.services.planning.constraint_checker import ConstraintChecker


def _check(
    checker: ConstraintChecker,
    *,
    plan_=None,
    constraints=None,
    accessibility_reqs=None,
    attractions=None,
    park_=None,
    live_context_=None,
):
    return checker.check(
        plan_ if plan_ is not None else plan(),
        constraints if constraints is not None else party_constraints(),
        accessibility_reqs if accessibility_reqs is not None else [],
        attractions if attractions is not None else {"a1": attraction()},
        park_ if park_ is not None else park(),
        live_context_ if live_context_ is not None else live_context(),
    )


def test_valid_plan_passes_with_no_violations():
    checker = ConstraintChecker()
    result = _check(checker)
    assert result.valid is True
    assert result.violations == []


def test_opening_hours_violation():
    checker = ConstraintChecker()
    early_park = park(opening_time=NOW + timedelta(hours=1))
    result = _check(checker, park_=early_park)
    assert result.valid is False
    assert result.violations[0].rule == RuleId.OPENING_HOURS


def test_height_violation_when_guest_too_short():
    checker = ConstraintChecker()
    constraints = party_constraints(guests=[guest(height_cm=100)])
    result = _check(checker, constraints=constraints)
    assert result.valid is False
    assert any(v.rule == RuleId.HEIGHT for v in result.violations)


def test_height_fails_closed_when_guest_height_unknown():
    checker = ConstraintChecker()
    constraints = party_constraints(guests=[guest(height_cm=None)])
    result = _check(checker, constraints=constraints)
    assert result.valid is False
    assert any(v.rule == RuleId.HEIGHT for v in result.violations)


def test_show_arrival_violation_when_no_showtime_matches_window():
    checker = ConstraintChecker()
    show_stop = stop(node_id="show1", kind=StopKind.SHOW)
    candidate_plan = plan(stops=[show_stop])
    context = live_context(showtimes={"show1": [NOW + timedelta(hours=2)]})
    result = _check(checker, plan_=candidate_plan, live_context_=context)
    assert result.valid is False
    assert any(v.rule == RuleId.SHOW_ARRIVAL for v in result.violations)


def test_must_do_violation_when_attraction_missing():
    checker = ConstraintChecker()
    constraints = party_constraints(must_do=["b2"])
    result = _check(checker, constraints=constraints)
    assert result.valid is False
    assert any(v.rule == RuleId.MUST_DO for v in result.violations)


def test_must_do_satisfied_via_unmet_must_do_list():
    checker = ConstraintChecker()
    constraints = party_constraints(must_do=["b2"])
    candidate_plan = plan(unmet_must_do=["b2"])
    context = live_context(
        statuses={"a1": AttractionStatus.OPERATING, "b2": AttractionStatus.CLOSED}
    )
    result = _check(
        checker, plan_=candidate_plan, constraints=constraints, live_context_=context
    )
    assert not any(v.rule == RuleId.MUST_DO for v in result.violations)


def test_must_do_violation_when_unmet_attraction_is_actually_operating():
    """Done-when [C23]: an OPERATING attraction listed as unmet must fail."""
    checker = ConstraintChecker()
    constraints = party_constraints(must_do=["b2"])
    candidate_plan = plan(unmet_must_do=["b2"])
    context = live_context(
        statuses={"a1": AttractionStatus.OPERATING, "b2": AttractionStatus.OPERATING}
    )
    result = _check(
        checker, plan_=candidate_plan, constraints=constraints, live_context_=context
    )
    assert result.valid is False
    assert any(v.rule == RuleId.MUST_DO for v in result.violations)


def test_must_do_violation_when_unmet_attraction_status_unknown():
    """Done-when [C23]: an unknown status is not a traceable unavailability reason."""
    checker = ConstraintChecker()
    constraints = party_constraints(must_do=["b2"])
    candidate_plan = plan(unmet_must_do=["b2"])
    result = _check(checker, plan_=candidate_plan, constraints=constraints)
    assert result.valid is False
    assert any(v.rule == RuleId.MUST_DO for v in result.violations)


def test_avoid_violation_when_plan_includes_avoided_attraction():
    checker = ConstraintChecker()
    constraints = party_constraints(avoid=["a1"])
    result = _check(checker, constraints=constraints)
    assert result.valid is False
    assert any(v.rule == RuleId.AVOID for v in result.violations)


def test_walking_budget_violation_when_total_exceeds_party_budget():
    checker = ConstraintChecker()
    constraints = party_constraints(party_walking_budget_minutes=1)
    result = _check(checker, constraints=constraints)
    assert result.valid is False
    assert any(v.rule == RuleId.WALKING_BUDGET for v in result.violations)


def test_lunch_window_violation_when_no_meal_stop_within_window():
    checker = ConstraintChecker()
    constraints = party_constraints(
        lunch_window=TimeWindow(
            start=NOW + timedelta(hours=3), end=NOW + timedelta(hours=4)
        )
    )
    result = _check(checker, constraints=constraints)
    assert result.valid is False
    assert any(v.rule == RuleId.LUNCH_WINDOW for v in result.violations)


def test_departure_violation_when_last_stop_ends_after_departure_time():
    checker = ConstraintChecker()
    constraints = party_constraints(departure_time=NOW)
    result = _check(checker, constraints=constraints)
    assert result.valid is False
    assert any(v.rule == RuleId.DEPARTURE for v in result.violations)


def test_accessibility_violation_when_walking_exceeds_guest_limit():
    checker = ConstraintChecker()
    reqs = [accessibility(daily_walking_limit_minutes=1)]
    result = _check(checker, accessibility_reqs=reqs)
    assert result.valid is False
    assert any(v.rule == RuleId.ACCESSIBILITY for v in result.violations)


def test_accessibility_violation_when_rest_frequency_exceeded():
    checker = ConstraintChecker()
    reqs = [accessibility(daily_walking_limit_minutes=None, rest_frequency_minutes=10)]
    result = _check(checker, accessibility_reqs=reqs)
    assert result.valid is False
    assert any(v.rule == RuleId.ACCESSIBILITY for v in result.violations)


def test_ride_restriction_fails_closed_when_no_notice_on_file():
    """Done-when: missing safety requirement data excludes the candidate."""
    checker = ConstraintChecker()
    reqs = [accessibility()]
    context = live_context(accessibility_results=[])
    result = _check(checker, accessibility_reqs=reqs, live_context_=context)
    assert result.valid is False
    assert any(v.rule == RuleId.RIDE_RESTRICTION for v in result.violations)


def test_ride_restriction_violation_when_check_is_ineligible():
    checker = ConstraintChecker()
    reqs = [accessibility()]
    context = live_context(
        accessibility_results=[
            accessibility_check(eligible=False, conflicting_requirement="X")
        ]
    )
    result = _check(checker, accessibility_reqs=reqs, live_context_=context)
    assert result.valid is False
    assert any(v.rule == RuleId.RIDE_RESTRICTION for v in result.violations)


def test_ride_restriction_passes_when_check_is_eligible():
    checker = ConstraintChecker()
    reqs = [accessibility()]
    context = live_context(accessibility_results=[accessibility_check(eligible=True)])
    result = _check(checker, accessibility_reqs=reqs, live_context_=context)
    assert not any(v.rule == RuleId.RIDE_RESTRICTION for v in result.violations)


def test_data_freshness_violation_when_snapshot_is_stale():
    checker = ConstraintChecker()
    context = live_context(retrieved_at=NOW - timedelta(hours=2))
    result = _check(checker, live_context_=context)
    assert result.valid is False
    assert any(v.rule == RuleId.DATA_FRESHNESS for v in result.violations)


def test_data_freshness_violation_when_attraction_is_closed():
    """Done-when: a plan containing a closed attraction fails validation."""
    checker = ConstraintChecker()
    context = live_context(statuses={"a1": AttractionStatus.CLOSED})
    result = _check(checker, live_context_=context)
    assert result.valid is False
    assert any(v.rule == RuleId.DATA_FRESHNESS for v in result.violations)


def test_data_freshness_violation_when_attraction_status_unknown():
    checker = ConstraintChecker()
    context = live_context(statuses={})
    result = _check(checker, live_context_=context)
    assert result.valid is False
    assert any(v.rule == RuleId.DATA_FRESHNESS for v in result.violations)


def test_accessibility_violation_when_heat_sensitive_guest_at_hot_outdoor_stop():
    """Done-when: a heat-sensitive guest is not scheduled at an outdoor stop above the threshold."""
    checker = ConstraintChecker()
    reqs = [
        accessibility(
            daily_walking_limit_minutes=None,
            mobility_requirements=[],
            ride_restrictions=[],
            heat_sensitivity=True,
        )
    ]
    outdoor_attraction = attraction(outdoor=True)
    context = live_context(
        weather=[
            WeatherHour(
                timestamp=NOW,
                condition="sunny",
                temperature_f=95.0,
                precipitation_probability=0.0,
            )
        ]
    )
    result = _check(
        checker,
        accessibility_reqs=reqs,
        attractions={"a1": outdoor_attraction},
        live_context_=context,
    )
    assert result.valid is False
    assert any(v.rule == RuleId.ACCESSIBILITY for v in result.violations)


def test_accessibility_passes_when_heat_sensitive_guest_at_cool_outdoor_stop():
    checker = ConstraintChecker()
    reqs = [
        accessibility(
            daily_walking_limit_minutes=None,
            mobility_requirements=[],
            ride_restrictions=[],
            heat_sensitivity=True,
        )
    ]
    outdoor_attraction = attraction(outdoor=True)
    context = live_context(
        weather=[
            WeatherHour(
                timestamp=NOW,
                condition="mild",
                temperature_f=70.0,
                precipitation_probability=0.0,
            )
        ]
    )
    result = _check(
        checker,
        accessibility_reqs=reqs,
        attractions={"a1": outdoor_attraction},
        live_context_=context,
    )
    assert result.valid is True
    assert not any(v.rule == RuleId.ACCESSIBILITY for v in result.violations)


def test_accessibility_passes_when_hot_stop_is_indoor():
    checker = ConstraintChecker()
    reqs = [
        accessibility(
            daily_walking_limit_minutes=None,
            mobility_requirements=[],
            ride_restrictions=[],
            heat_sensitivity=True,
        )
    ]
    indoor_attraction = attraction(outdoor=False)
    context = live_context(
        weather=[
            WeatherHour(
                timestamp=NOW,
                condition="sunny",
                temperature_f=95.0,
                precipitation_probability=0.0,
            )
        ]
    )
    result = _check(
        checker,
        accessibility_reqs=reqs,
        attractions={"a1": indoor_attraction},
        live_context_=context,
    )
    assert result.valid is True
    assert not any(v.rule == RuleId.ACCESSIBILITY for v in result.violations)


def test_data_freshness_violation_when_accessibility_coverage_incomplete():
    checker = ConstraintChecker()
    context = live_context(
        coverage=CoverageReport(
            required_attractions_covered=True,
            required_shows_covered=True,
            weather_covered=True,
            accessibility_checks_complete=False,
        )
    )
    result = _check(checker, live_context_=context)
    assert result.valid is False
    assert any(v.rule == RuleId.DATA_FRESHNESS for v in result.violations)


def test_high_preference_score_cannot_override_a_hard_restriction():
    """Done-when: a high preference/utility score never buys back a hard violation."""
    checker = ConstraintChecker()
    high_utility_plan = plan(
        objective_value=999_999.0,
        per_guest_satisfaction={"g1": 1.0},
        stops=[stop(utility=999_999.0)],
    )
    reqs = [accessibility()]
    context = live_context(accessibility_results=[])  # no notice on file -> fail closed

    result = _check(
        checker, plan_=high_utility_plan, accessibility_reqs=reqs, live_context_=context
    )

    assert result.valid is False
    assert any(v.rule == RuleId.RIDE_RESTRICTION for v in result.violations)
