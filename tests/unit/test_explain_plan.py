"""EXPLAIN runs only for a plan that passed the checker."""

import factories
import pytest
from planning_support import PlanningAttractionRepository, factory_for, make_deps

from parkmind.core.contracts import (
    CheckResult,
    ConstraintViolation,
    EventThresholds,
    FairnessConfig,
    GroupObjective,
    HardConstraintSet,
    PlanDiff,
    PreferenceSource,
    RuleId,
)
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.use_cases.explain_plan import (
    ExplainBeforeValidationError,
    ExplainPlanUseCase,
    explain_plan,
    explanation_payload,
)

INVALID = CheckResult(
    valid=False,
    violations=[ConstraintViolation(rule=RuleId.MUST_DO, message="missing")],
)


def test_explaining_an_unvalidated_plan_raises():
    with pytest.raises(ExplainBeforeValidationError):
        explain_plan(factories.plan(), INVALID, {})
    with pytest.raises(ExplainBeforeValidationError):
        ExplainPlanUseCase(factory_for(make_deps())).prepare(factories.plan(), INVALID)


def test_the_explanation_lists_each_stop_with_its_name_wait_and_walk():
    text = explain_plan(
        factories.plan(), CheckResult(valid=True), {"a1": "Space Mountain"}
    )

    assert "Space Mountain (20 min wait, 5 min walk)" in text
    assert text.startswith("1 stops, 20 min of waiting and 5 min of walking")


def test_unmet_must_dos_are_called_out_by_name():
    plan = factories.plan(unmet_must_do=["id-tron"])

    text = explain_plan(plan, CheckResult(valid=True), {"id-tron": "TRON"})

    assert "Could not fit your must-dos today: TRON." in text


def test_the_use_case_resolves_names_from_the_catalog():
    plan = factories.plan(stops=[factories.stop(node_id="id-tron")])

    text = (
        ExplainPlanUseCase(factory_for(make_deps()))
        .prepare(plan, CheckResult(valid=True))
        .template
    )

    assert "TRON" in text


def test_a_meal_without_a_restaurant_reads_as_lunch_not_as_a_placeholder_id():
    from parkmind.core.contracts import StopKind
    from parkmind.services.planning.optimizer import MEAL_VENUE_TBD

    plan = factories.plan(
        stops=[factories.stop(node_id=MEAL_VENUE_TBD, kind=StopKind.MEAL)]
    )

    text = explain_plan(plan, CheckResult(valid=True), {})

    assert "Lunch" in text and MEAL_VENUE_TBD not in text


# --- who sits out a height-restricted ride ---------------------------------------

ADULT = factories.guest(guest_id="g1", height_cm=175)
CHILD = factories.guest(guest_id="g2", role="child", height_cm=90)


def _restricted_plan(served: list[str]):
    return factories.plan(stops=[factories.stop(node_id="a1", served_guests=served)])


def test_a_guest_too_short_for_a_ride_is_named_as_skipping_it():
    text = explain_plan(
        _restricted_plan(["g1"]),
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        guests=[ADULT, CHILD],
        height_limits={"a1": 112},
    )

    assert "Guest 2 (90 cm) will skip Space Mountain (minimum height 112 cm)" in text
    assert "Guest 1" not in text


def test_nothing_is_said_when_everyone_rides():
    text = explain_plan(
        _restricted_plan(["g1", "g2"]),
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        guests=[ADULT, factories.guest(guest_id="g2", height_cm=130)],
        height_limits={"a1": 112},
    )

    assert "skip" not in text


def test_a_whole_party_stop_or_an_unrestricted_ride_adds_no_line():
    names = {"a1": "Space Mountain"}
    whole_party = explain_plan(
        _restricted_plan([]),
        CheckResult(valid=True),
        names,
        guests=[ADULT, CHILD],
        height_limits={"a1": 112},
    )
    unrestricted = explain_plan(
        _restricted_plan(["g1"]), CheckResult(valid=True), names, guests=[ADULT, CHILD]
    )

    assert "skip" not in whole_party and "skip" not in unrestricted


def test_a_guest_with_unknown_height_who_does_not_ride_is_named_too():
    unknown = factories.guest(guest_id="g2", height_cm=None)

    text = explain_plan(
        _restricted_plan(["g1"]),
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        guests=[ADULT, unknown],
        height_limits={"a1": 112},
    )

    assert "Guest 2 will skip Space Mountain" in text


def test_the_use_case_reads_the_height_limits_from_the_catalog():
    plan = _restricted_plan(["g1"])
    catalog = PlanningAttractionRepository(
        [factories.attraction(height_restriction_cm=112)]
    )

    text = (
        ExplainPlanUseCase(factory_for(make_deps(attractions=catalog)))
        .prepare(plan, CheckResult(valid=True), guests=[ADULT, CHILD])
        .template
    )

    assert "Guest 2 (90 cm) will skip Space Mountain (minimum height 112 cm)" in text


# --- the payload the LLM explains from -----------------------

OBJECTIVE = GroupObjective(
    objective_version="t",
    weights={"affinity:thrill": 0.8, "lambda_queue": 0.333, "lambda_walk": 0.2},
    weight_provenance={
        "affinity:thrill": PreferenceSource.STATED,
        "lambda_queue": PreferenceSource.LEARNED,
    },
    hard_constraints=HardConstraintSet(),
    fairness=FairnessConfig(lambda_fairness=0.3, min_satisfaction_floor=0.0),
    event_thresholds=EventThresholds(),
)


def _payload(**overrides):
    fields = {
        "names": {"a1": "Space Mountain", "id-tron": "TRON"},
        "must_do": ["a1", "id-tron"],
        "objective": OBJECTIVE,
    }
    fields.update(overrides)
    return explanation_payload(
        overrides.pop("plan", factories.plan(unmet_must_do=["id-tron"])),
        CheckResult(valid=True),
        **{k: v for k, v in fields.items() if k != "plan"},
    )


def test_every_preference_weight_is_labelled_stated_learned_or_default():
    signals = {p.weight: (p.value, p.source) for p in _payload().preferences}

    assert signals == {
        "affinity:thrill": (0.8, "stated"),
        "lambda_queue": (0.33, "learned"),
        "lambda_walk": (0.2, "default"),
    }


def test_the_payload_says_which_must_dos_are_in_the_plan_and_which_could_not_fit():
    payload = _payload()

    assert payload.must_do_in_plan == ["Space Mountain"]
    assert payload.unmet_must_do == ["TRON"]
    assert [s.must_do for s in payload.stops] == [True]


def test_the_payload_shows_figures_as_the_template_prints_them_and_names_not_ids():
    stop = factories.stop(
        node_id="a1", expected_wait_minutes=20.4, walking_minutes=4.6, utility=0.8125
    )
    payload = _payload(plan=factories.plan(stops=[stop]))

    [facts] = payload.stops
    assert (facts.name, facts.wait_minutes, facts.walk_minutes) == (
        "Space Mountain",
        20,
        5,
    )
    assert facts.preference_score == 0.81
    assert "a1" not in payload.to_json()


def test_who_sits_out_a_ride_is_in_the_payload_but_never_the_guests_height():
    """Review #90 (4): the narrator gets the ride's minimum, not a child's height."""
    payload = _payload(
        plan=_restricted_plan(["g1"]),
        guests=[ADULT, CHILD],
        height_limits={"a1": 112},
    )

    [facts] = payload.stops
    assert [(o.guest, o.minimum_height_cm) for o in facts.sitting_out] == [
        ("Guest 2", 112)
    ]
    assert "90" not in payload.to_json() and "175" not in payload.to_json()


def test_the_narrator_payload_never_carries_a_guests_free_text_label():
    """Review #91 (6): "tu hijo" stays in the in-process template, not the LLM payload."""
    kid = factories.guest(guest_id="g2", role="child", height_cm=90, label="tu hijo")
    plan = _restricted_plan(["g1"])

    payload = _payload(plan=plan, guests=[ADULT, kid], height_limits={"a1": 112})
    template = explain_plan(
        plan,
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        guests=[ADULT, kid],
        height_limits={"a1": 112},
    )

    assert "hijo" not in payload.to_json()
    assert [o.guest for o in payload.stops[0].sitting_out] == ["Guest 2"]
    assert "tu hijo" in template


def test_a_guest_id_that_is_not_gN_is_numbered_by_position_never_passed_through():
    """An id could carry a name ("maria-lopez"): the narrator gets "Guest N" instead."""
    kid = factories.guest(guest_id="maria-lopez", role="child", height_cm=90)
    plan = _restricted_plan(["g1"])

    payload = _payload(plan=plan, guests=[ADULT, kid], height_limits={"a1": 112})
    template = explain_plan(
        plan,
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        guests=[ADULT, kid],
        height_limits={"a1": 112},
    )

    assert [o.guest for o in payload.stops[0].sitting_out] == ["Guest 2"]
    assert "maria" not in payload.to_json() and "maria" not in template


def test_a_replan_payload_carries_its_reason_and_the_named_changes():
    payload = _payload(
        reason="Space Mountain is closed.",
        diff=PlanDiff(stops_removed=["id-tron"], stops_added=["a1"]),
    )

    assert payload.reason == "Space Mountain is closed."
    assert payload.changes is not None
    assert payload.changes["removed"] == ["TRON"]
    assert payload.changes["added"] == ["Space Mountain"]


def test_no_payload_for_an_unvalidated_plan():
    with pytest.raises(ExplainBeforeValidationError):
        explanation_payload(factories.plan(), INVALID, {})
    with pytest.raises(ExplainBeforeValidationError):
        ExplainPlanUseCase(factory_for(make_deps())).prepare(factories.plan(), INVALID)


def test_prepare_gives_the_payload_and_the_template_from_one_catalog_read():
    plan = factories.plan(stops=[factories.stop(node_id="id-tron")])

    prepared = ExplainPlanUseCase(factory_for(make_deps())).prepare(
        plan, CheckResult(valid=True), must_do=["id-tron"], objective=OBJECTIVE
    )

    assert prepared.payload.stops[0].name == "TRON"
    assert "TRON" in prepared.template


def test_a_replan_with_no_reason_does_not_open_with_a_blank_line():
    """Review #90: an empty reason used to leave an empty first line."""
    from parkmind.services.use_cases.explain_plan import explain_replan

    text = explain_replan(
        factories.plan(),
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        reason="",
        diff=PlanDiff(stops_added=["a1"]),
    )

    assert text.startswith("Added: Space Mountain.")


@pytest.mark.parametrize(
    "served, checker_flags_child, explain_names_child",
    [
        ([], True, False),
        (["g1", "g2"], True, False),
        (["g1"], False, True),
    ],
)
def test_the_checker_and_the_explanation_read_served_guests_alike(
    served, checker_flags_child, explain_names_child
):
    """Empty means the whole party for both: nobody sits out, so a short guest is a violation."""
    constraints = factories.party_constraints(party_size=2, guests=[ADULT, CHILD])
    plan = _restricted_plan(served)

    result = ConstraintChecker().check(
        plan,
        constraints,
        [],
        {"a1": factories.attraction(height_restriction_cm=112)},
        factories.park(),
        factories.live_context(),
        factories.NOW,
    )
    text = explain_plan(
        plan,
        CheckResult(valid=True),
        {"a1": "Space Mountain"},
        guests=[ADULT, CHILD],
        height_limits={"a1": 112},
    )

    flagged = [v for v in result.violations if v.rule == RuleId.HEIGHT and "g2" in v.message]
    assert bool(flagged) is checker_flags_child
    assert ("Guest 2 (90 cm) will skip" in text) is explain_names_child
