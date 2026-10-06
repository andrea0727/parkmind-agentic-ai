"""PreferenceScorer (P0-17): utility per attraction from the group objective and each profile."""

import ast
import random
from pathlib import Path

import pytest
from factories import (
    attraction,
    guest_profile,
    live_context,
    park,
    plan,
    preference,
    stop,
)

from parkmind.core.contracts import (
    AttractionCategory,
    AttractionStatus,
    EventThresholds,
    FairnessConfig,
    GroupObjective,
    HardConstraintSet,
    RideRestriction,
    SensitivityKind,
    SensitivityLevel,
    StopKind,
    WaitEstimate,
)
from parkmind.services.clients.knowledge.in_memory import InMemoryKnowledgeStore
from parkmind.services.clients.knowledge.safety_notices import (
    magic_kingdom_knowledge_store,
)
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
)
from parkmind.services.personalization.group_preference_resolver import (
    DEFAULT_QUEUE_TOLERANCE,
    DEFAULT_WALKING_TOLERANCE,
)
from parkmind.services.personalization.preference_scorer import (
    PREFERENCE_SCORER_VERSION,
    PreferenceScorer,
    ScoringConfig,
    fairness_gap,
    per_guest_satisfaction,
)
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import RoutingNotFoundError
from parkmind.services.use_cases.score_preferences import ScorePreferencesUseCase

SRC = Path(__file__).resolve().parents[2] / "src" / "parkmind"

COASTER = attraction(node_id="coaster", category=AttractionCategory.THRILL, land="Tomorrowland",
                     typical_wait_minutes=60)
CAROUSEL = attraction(node_id="carousel", category=AttractionCategory.FAMILY, land="Fantasyland",
                      height_restriction_cm=None, typical_wait_minutes=10)
DARK = attraction(node_id="dark", category=AttractionCategory.DARK_RIDE, land="Fantasyland",
                  height_restriction_cm=None, typical_wait_minutes=20)
SHOW = attraction(node_id="show", category=AttractionCategory.SHOW, land="Main Street, U.S.A.",
                  height_restriction_cm=None, typical_wait_minutes=0)
CATALOG = [COASTER, CAROUSEL, DARK, SHOW]
NOTICES = InMemoryKnowledgeStore(
    {"coaster": [RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE], "carousel": [], "dark": [], "show": []},
    corpus_version="test",
)


def _objective(eligible: dict[str, list[str]], lambda_fairness: float = 0.0) -> GroupObjective:
    return GroupObjective(
        objective_version="1",
        weights={},
        per_guest_eligible=eligible,
        hard_constraints=HardConstraintSet(),
        fairness=FairnessConfig(lambda_fairness=lambda_fairness, min_satisfaction_floor=0.0),
        event_thresholds=EventThresholds(),
    )


def _context(waits: dict[str, float] | None = None):  # type: ignore[no-untyped-def]
    waits = {"coaster": 30.0, "carousel": 30.0, "dark": 30.0} if waits is None else waits
    return live_context(
        waits={
            a: WaitEstimate(attraction_id=a, wait_minutes=w, status=AttractionStatus.OPERATING)
            for a, w in waits.items()
        },
        statuses={a: AttractionStatus.OPERATING for a in waits},
    )


ALL = {"g1": ["coaster", "carousel", "dark", "show"]}


def _score(profiles, eligible=ALL, *, lambda_fairness=0.0, context=None, **kwargs):  # type: ignore[no-untyped-def]
    return PreferenceScorer(kwargs.pop("config", None)).score(
        objective=_objective(eligible, lambda_fairness),
        profiles=profiles,
        attractions=kwargs.pop("attractions", CATALOG),
        live_context=context if context is not None else _context(),
        knowledge=kwargs.pop("knowledge", NOTICES),
        **kwargs,
    )


def _u(scores, node: str, guest: str = "g1") -> float:  # type: ignore[no-untyped-def]
    return scores.per_guest[guest][node]


class _Routing:
    def __init__(self, minutes: dict[str, float]) -> None:
        self.minutes = minutes

    def walk_minutes(self, origin_node_id: str, destination_node_id: str) -> float:
        if destination_node_id not in self.minutes:
            raise RoutingNotFoundError(destination_node_id)
        return self.minutes[destination_node_id]


def _graph(minutes: dict[str, float], aliases: dict[str, str] | None = None) -> ParkGraph:
    return ParkGraph.from_sources(
        routing=_Routing(minutes), park=park(), attractions=CATALOG, land_aliases=aliases
    )


# --- Done-when: same inputs produce the same score -----------------------------------


def test_same_inputs_give_the_same_scores_in_any_input_order() -> None:
    profiles = [guest_profile(guest_id=g, queue_tolerance=preference(t)) for g, t in (("g1", 0.2), ("g2", 0.9))]
    eligible = {"g1": ["coaster", "carousel"], "g2": ["carousel", "dark", "show"]}
    first = _score(profiles, eligible, lambda_fairness=0.5)

    shuffled = list(CATALOG)
    random.Random(7).shuffle(shuffled)
    again = _score(list(reversed(profiles)), eligible, lambda_fairness=0.5, attractions=shuffled)

    assert first == again
    assert list(first.group) == sorted(first.group)


# --- Done-when: weights move the ranking in the expected direction (and per dimension) --


@pytest.mark.parametrize("level", list(SensitivityLevel))
def test_intensity_sensitivity_ranks_a_flagged_ride_below_a_gentle_one(level: SensitivityLevel) -> None:
    plain = _score([guest_profile()])
    sensitive = _score([guest_profile(sensitivities={SensitivityKind.INTENSITY: level})])

    assert _u(plain, "coaster") == pytest.approx(_u(plain, "carousel"))  # same wait, no preference
    assert _u(sensitive, "coaster") < _u(sensitive, "carousel")
    assert _u(sensitive, "carousel") == pytest.approx(_u(plain, "carousel"))


def test_intensity_penalty_grows_with_the_guest_level() -> None:
    penalties = [
        _u(_score([guest_profile()]), "coaster")
        - _u(_score([guest_profile(sensitivities={SensitivityKind.INTENSITY: level})]), "coaster")
        for level in (SensitivityLevel.LOW, SensitivityLevel.MEDIUM, SensitivityLevel.HIGH)
    ]

    assert penalties == pytest.approx([0.25, 0.5, 1.0])


def test_intensity_comes_from_the_notice_then_the_category() -> None:
    family_flagged = attraction(node_id="barn", category=AttractionCategory.FAMILY, height_restriction_cm=None)
    thrill_unflagged = attraction(node_id="mild", category=AttractionCategory.THRILL)
    thrill_no_notice = attraction(node_id="unknown", category=AttractionCategory.THRILL)
    knowledge = InMemoryKnowledgeStore(
        {"barn": [RideRestriction.NOT_RECOMMENDED_MOTION_SENSITIVITY], "mild": [RideRestriction.NOT_RECOMMENDED_EXPECTANT]},
        corpus_version="test",
    )
    catalog = [family_flagged, thrill_unflagged, thrill_no_notice]
    eligible = {"g1": ["barn", "mild", "unknown"]}
    context = _context({"barn": 10.0, "mild": 10.0, "unknown": 10.0})

    def penalty(node: str) -> float:
        plain = _score([guest_profile()], eligible, context=context, attractions=catalog, knowledge=knowledge)
        high = _score(
            [guest_profile(sensitivities={SensitivityKind.INTENSITY: SensitivityLevel.HIGH})],
            eligible, context=context, attractions=catalog, knowledge=knowledge,
        )
        return _u(plain, node) - _u(high, node)

    assert (penalty("barn"), penalty("mild"), penalty("unknown")) == pytest.approx((1.0, 0.5, 0.5))


def test_darkness_and_water_sensitivities_follow_the_category() -> None:
    plain = _score([guest_profile()])
    afraid = _score([guest_profile(sensitivities={SensitivityKind.DARKNESS: SensitivityLevel.HIGH})])

    assert _u(plain, "dark") - _u(afraid, "dark") == pytest.approx(1.0)
    assert _u(plain, "carousel") == pytest.approx(_u(afraid, "carousel"))


def test_low_queue_tolerance_drops_a_long_wait_ride_further() -> None:
    context = _context({"coaster": 90.0, "carousel": 10.0, "dark": 30.0})

    def spread(tolerance: float) -> float:
        scores = _score([guest_profile(queue_tolerance=preference(tolerance))], context=context)
        return _u(scores, "carousel") - _u(scores, "coaster")

    assert spread(0.1) > spread(0.5) > spread(0.9) > 0


def test_low_walking_comfort_drops_a_far_ride_further() -> None:
    graph = _graph({"coaster": 25.0, "carousel": 2.0, "dark": 10.0, "show": 5.0})

    def spread(tolerance: float) -> float:
        scores = _score(
            [guest_profile(walking_tolerance=preference(tolerance))], park_graph=graph, origin_node_id="show"
        )
        return _u(scores, "carousel") - _u(scores, "coaster")

    assert spread(0.1) > spread(0.5) > spread(0.9)


def test_without_an_origin_there_is_no_walking_term() -> None:
    graph = _graph({"coaster": 25.0, "carousel": 2.0, "dark": 10.0, "show": 5.0})
    profile = guest_profile(walking_tolerance=preference(0.0))

    assert _score([profile], park_graph=graph) == _score([profile])


def test_an_unknown_route_gets_no_walking_term_and_is_reported() -> None:
    graph = _graph({"carousel": 2.0, "dark": 10.0})
    profile = guest_profile(walking_tolerance=preference(0.0))

    scores = _score([profile], park_graph=graph, origin_node_id="show")

    assert scores.unrouted == ("coaster",)
    assert _u(scores, "coaster") == pytest.approx(_u(_score([profile]), "coaster"))
    assert _u(scores, "show") == pytest.approx(_u(_score([profile]), "show"))  # the origin itself: 0 min


@pytest.mark.parametrize("value", [1.0, -1.0])
def test_a_land_affinity_lifts_or_lowers_that_land(value: float) -> None:
    plain = _score([guest_profile()])
    fan = _score([guest_profile(thematic_affinity={"Fantasyland": preference(value)})])

    assert _u(fan, "carousel") - _u(plain, "carousel") == pytest.approx(0.5 * value)
    assert _u(fan, "dark") - _u(plain, "dark") == pytest.approx(0.5 * value)
    assert _u(fan, "coaster") == pytest.approx(_u(plain, "coaster"))


def test_affinity_keys_match_park_aliases_and_categories_and_report_the_rest() -> None:
    graph = _graph({}, aliases={"tomorrow land": "Tomorrowland"})
    profile = guest_profile(
        thematic_affinity={"Tomorrow  Land": preference(1.0), "dark ride": preference(1.0), "Narnia": preference(1.0)}
    )

    scores = _score([profile], park_graph=graph)
    plain = _score([guest_profile()])

    assert _u(scores, "coaster") - _u(plain, "coaster") == pytest.approx(0.5)
    assert _u(scores, "dark") - _u(plain, "dark") == pytest.approx(0.5)
    assert scores.unmatched_affinities == ("g1:Narnia",)


def test_preferred_and_avoided_categories_move_the_ranking() -> None:
    likes = _score([guest_profile(preferred_categories=[AttractionCategory.DARK_RIDE])])
    dislikes = _score([guest_profile(avoided_categories=[AttractionCategory.DARK_RIDE])])

    assert _u(likes, "dark") > _u(likes, "carousel")
    assert _u(dislikes, "dark") < _u(dislikes, "carousel")


# --- Done-when: hard constraints are not encoded as score penalties -------------------


def test_an_ineligible_guest_neither_raises_nor_dilutes_the_riders_mean() -> None:
    adult = guest_profile(guest_id="g1")
    child = guest_profile(guest_id="g2", avoided_categories=[AttractionCategory.THRILL])
    party = {"g1": ["coaster", "carousel"], "g2": ["carousel"]}

    scores = _score([adult, child], party)  # lambda_fairness = 0: no unserved cost

    assert "coaster" not in scores.per_guest["g2"]  # never scored for the child, not scored negative
    assert scores.group["coaster"] == pytest.approx(_u(scores, "coaster", "g1"))


def test_an_attraction_nobody_may_ride_is_left_out_not_penalized() -> None:
    scores = _score([guest_profile()], {"g1": ["carousel"]})

    assert set(scores.group) == {"carousel"}
    assert "coaster" not in scores.utilities()


# --- Fairness: section 17's "guest left unserved" penalty ------------------------------


@pytest.mark.parametrize("lambda_fairness", [0.0, 0.5, 2.0])
def test_leaving_a_guest_out_costs_lambda_times_the_unserved_share(lambda_fairness: float) -> None:
    profiles = [guest_profile(guest_id="g1"), guest_profile(guest_id="g2")]
    context = _context({"coaster": 30.0, "carousel": 30.0})
    party = {"g1": ["coaster", "carousel"], "g2": ["carousel"]}

    scores = _score(profiles, party, lambda_fairness=lambda_fairness, context=context)

    shared = scores.group["carousel"]
    partial = scores.group["coaster"]
    assert _u(scores, "carousel", "g1") == pytest.approx(_u(scores, "coaster", "g1"))  # equal enjoyment
    assert shared - partial == pytest.approx(lambda_fairness / 2)  # one guest of two left out, charged once


# --- Done-when: a smaller eligible set is not structurally penalized (C20) ------------


def test_a_child_with_few_eligible_rides_is_not_behind_in_the_fairness_gap() -> None:
    rides = [f"r{i}" for i in range(6)]
    catalog = [
        attraction(node_id=r, category=AttractionCategory.FAMILY, height_restriction_cm=None) for r in rides
    ]
    context = _context({r: 10.0 + i for i, r in enumerate(rides)})
    party = {"adult": rides, "child": rides[:2]}  # the child may ride two of six
    profiles = [guest_profile(guest_id="adult"), guest_profile(guest_id="child")]
    scores = _score(profiles, party, context=context, attractions=catalog)
    day = plan(stops=[stop(node_id=r, served_guests=["adult", "child"]) for r in rides[:2]])

    satisfaction = per_guest_satisfaction(day, scores)

    assert satisfaction == pytest.approx({"adult": 1.0, "child": 1.0})
    assert fairness_gap(satisfaction) == pytest.approx(0.0)
    count_based = {"adult": 2 / len(rides), "child": 2 / 2}  # visited / eligible-set size
    assert fairness_gap(count_based) > 0.5  # the normalization this replaces


def test_satisfaction_counts_only_stops_the_guest_is_served_at() -> None:
    profiles = [guest_profile(guest_id="g1"), guest_profile(guest_id="g2")]
    scores = _score(profiles, {"g1": ["coaster", "carousel"], "g2": ["carousel"]})
    day = plan(stops=[
        stop(node_id="carousel", served_guests=["g1"]),  # g2 may ride it, but the plan leaves g2 out
        stop(node_id="carousel", kind=StopKind.REST, served_guests=["g1", "g2"]),
    ])

    satisfaction = per_guest_satisfaction(day, scores)

    assert satisfaction["g2"] == pytest.approx(0.0)  # not served; a REST at the same node is not a ride
    assert satisfaction["g1"] > 0
    assert fairness_gap(satisfaction) == pytest.approx(satisfaction["g1"])


def test_a_guest_with_nothing_positive_to_enjoy_scores_one() -> None:
    gloomy = guest_profile(avoided_categories=list(AttractionCategory), queue_tolerance=preference(0.0))
    scores = _score([gloomy], {"g1": ["coaster", "carousel", "dark"]},
                    context=_context({"coaster": 240.0, "carousel": 240.0, "dark": 240.0}))
    assert all(u <= 0 for u in scores.per_guest["g1"].values())

    assert per_guest_satisfaction(plan(stops=[stop(node_id="carousel")]), scores) == {"g1": 1.0}
    assert fairness_gap({}) == 0.0


@pytest.mark.parametrize(
    "stops",
    [[], [stop(node_id="carousel", kind=StopKind.MEAL), stop(node_id="dark", kind=StopKind.REST)]],
    ids=["empty plan", "only meals and rests"],
)
def test_a_guest_with_options_scores_zero_on_a_plan_with_no_rides(stops) -> None:  # type: ignore[no-untyped-def]
    scores = _score([guest_profile()])
    assert any(u > 0 for u in scores.per_guest["g1"].values())

    assert per_guest_satisfaction(plan(stops=stops), scores) == {"g1": 0.0}


# --- Edge cases --------------------------------------------------------------------------


def test_a_guest_without_a_profile_gets_the_resolver_defaults() -> None:
    default_like = guest_profile(
        queue_tolerance=preference(DEFAULT_QUEUE_TOLERANCE),
        walking_tolerance=preference(DEFAULT_WALKING_TOLERANCE),
    )
    graph = _graph({"coaster": 20.0, "carousel": 5.0, "dark": 9.0, "show": 1.0})

    assert _score([], park_graph=graph, origin_node_id="show") == _score(
        [default_like], park_graph=graph, origin_node_id="show"
    )


def test_profiles_outside_the_party_are_ignored() -> None:
    stranger = guest_profile(guest_id="nobody", preferred_categories=[AttractionCategory.THRILL])

    assert _score([guest_profile(), stranger]) == _score([guest_profile()])


def test_no_live_wait_is_a_small_risk_except_for_shows() -> None:
    live = _score([guest_profile()])
    missing = _score([guest_profile()], context=_context({"carousel": 30.0, "dark": 30.0}))
    config = ScoringConfig()

    expected_coaster = _u(live, "coaster") - (60 - 30) * (1 - 0.4) / 60 - config.risk_penalty
    assert _u(missing, "coaster") == pytest.approx(expected_coaster)  # typical 60 min, flagged as a guess
    assert _u(missing, "show") == pytest.approx(1.0)  # no queue, no guess: base utility only


def test_a_replan_prefers_stops_already_in_the_base_plan() -> None:
    base = plan(stops=[stop(node_id="carousel")])

    scores = _score([guest_profile()], base_plan=base)
    plain = _score([guest_profile()])

    assert _u(scores, "carousel") == pytest.approx(_u(plain, "carousel"))
    assert _u(plain, "dark") - _u(scores, "dark") == pytest.approx(ScoringConfig().change_penalty)


def test_result_carries_the_model_version_and_a_plain_utilities_dict() -> None:
    scores = _score([guest_profile()])

    assert scores.model_version == PREFERENCE_SCORER_VERSION
    assert type(scores.utilities()) is dict and scores.utilities() == dict(scores.group)


def test_negative_coefficients_are_refused() -> None:
    with pytest.raises(ValueError, match="queue_weight"):
        ScoringConfig(queue_weight=-1.0)


def test_use_case_delegates_to_the_scorer() -> None:
    kwargs = {
        "objective": _objective(ALL, 0.5),
        "profiles": [guest_profile()],
        "attractions": CATALOG,
        "live_context": _context(),
        "knowledge": NOTICES,
    }

    assert ScorePreferencesUseCase().execute(**kwargs) == PreferenceScorer().score(**kwargs)


# --- Real data: the P0-26a notices over the curated Magic Kingdom catalog ---------------


def test_real_notices_mark_five_rides_as_highly_intense() -> None:
    catalog = [
        attraction(node_id=aid, name=aid, **{k: v for k, v in meta.items() if k != "land"}, land=meta["land"])
        for aid, meta in MAGIC_KINGDOM_ATTRACTION_METADATA.items()
    ]
    eligible = {"g1": [a.node_id for a in catalog]}
    context = _context({})

    def scores(profile):  # type: ignore[no-untyped-def]
        return _score([profile], eligible, context=context, attractions=catalog,
                      knowledge=magic_kingdom_knowledge_store())

    plain = scores(guest_profile())
    high = scores(guest_profile(sensitivities={SensitivityKind.INTENSITY: SensitivityLevel.HIGH}))
    penalty = {a.node_id: _u(plain, a.node_id) - _u(high, a.node_id) for a in catalog}

    assert sum(p == pytest.approx(1.0) for p in penalty.values()) == 5
    assert all(penalty[a.node_id] > 0 for a in catalog if a.category == AttractionCategory.THRILL)
    assert all(penalty[a.node_id] == pytest.approx(0.0) for a in catalog if a.category == AttractionCategory.SHOW)


# --- `now` is never read; the scorer is pure core ------------------------------------------


def test_the_scorer_never_reads_the_clock() -> None:
    tree = ast.parse((SRC / "services/personalization/preference_scorer.py").read_text(encoding="utf-8"))
    names = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert not names & {"now", "utcnow", "today", "time", "datetime", "random"}
