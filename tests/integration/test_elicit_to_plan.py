"""elicit -> confirm -> preference scoring -> optimizer -> ConstraintChecker.

The guests say attraction *names*; the optimizer and the checker only understand
catalog ``node_id``s. These tests drive the whole chain from what a guest says,
so a name that never becomes an id (an avoided ride scheduled anyway, a must-do
never scheduled) shows up here.
"""

from datetime import datetime
from typing import Any

from elicit_support import FakeExtractor, catalog, make_intake, make_names, scenario
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from parkmind.core.contracts import (
    PARK_TZ,
    AccessibilityRequirements,
    Attraction,
    AttractionStatus,
    CoverageReport,
    FairnessConfig,
    LiveContext,
    Park,
    PartyConstraints,
    Plan,
    RuleId,
    StopKind,
    WaitEstimate,
)
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.elicitation_graph import build_elicitation_graph
from parkmind.services.clients.knowledge.in_memory import InMemoryKnowledgeStore
from parkmind.services.personalization.group_preference_resolver import (
    GroupPreferenceResolver,
)
from parkmind.services.personalization.preference_scorer import PreferenceScorer
from parkmind.services.planning.constraint_checker import ConstraintChecker
from parkmind.services.planning.forecast_service import ForecastService
from parkmind.services.planning.optimizer import GreedyInsertionOptimizer
from parkmind.services.planning.park_graph import ParkGraph

DAY = datetime.now(PARK_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
NOW = DAY.replace(hour=9, minute=5)
PARK = Park(park_id="mk", name="Magic Kingdom", opening_time=DAY.replace(hour=9), closing_time=DAY.replace(hour=22))
COMPLETE = scenario("wiki_example_complete")
RIDES = (StopKind.ATTRACTION, StopKind.SHOW)


class _FlatRouting:
    def walk_minutes(self, a: str, b: str) -> float:
        return 0.0 if a == b else 5.0


def _live_context(attractions: list[Attraction]) -> LiveContext:
    return LiveContext(
        snapshot_id="snap",
        retrieved_at=DAY.replace(hour=9),
        waits={
            a.node_id: WaitEstimate(
                attraction_id=a.node_id, wait_minutes=15.0, status=AttractionStatus.OPERATING
            )
            for a in attractions
        },
        statuses={a.node_id: AttractionStatus.OPERATING for a in attractions},
        showtimes={},
        coverage=CoverageReport(
            required_attractions_covered=True,
            required_shows_covered=True,
            weather_covered=True,
            accessibility_checks_complete=True,
        ),
    )


def _confirmed(extraction: dict[str, Any]) -> tuple[PartyConstraints, list[Any], list[AccessibilityRequirements]]:
    """What the guests said, confirmed with consent, as the planner receives it."""
    intake, store = make_intake()
    graph = build_elicitation_graph(
        FakeExtractor(extraction), intake, default_checkpointer(), names=make_names()
    )
    config: Any = {"configurable": {"thread_id": "t1"}}
    result = graph.invoke(
        {"thread_id": "t1", "messages": [HumanMessage(content=m) for m in COMPLETE["messages"]]},
        config=config,
    )
    assert result["__interrupt__"][0].value["kind"] == "hard_constraint_confirmation"
    result = graph.invoke(Command(resume={"confirmed": True, "consent": True}), config=config)
    assert "__interrupt__" not in result
    state = graph.get_state(config).values
    constraints = state["constraints"]
    accessibility = [r for g in constraints.guests if (r := store.get("t1", g.guest_id))]
    return constraints, state["guest_profiles"], accessibility


def _plan_and_check(extraction: dict[str, Any]):
    constraints, profiles, accessibility = _confirmed(extraction)
    attractions = catalog()
    live = _live_context(attractions)
    objective = GroupPreferenceResolver().resolve(
        guests=constraints.guests,
        profiles=profiles,
        accessibility=accessibility,
        attractions=attractions,
        party_constraints=constraints,
        live_context=live,
        fairness=FairnessConfig(lambda_fairness=0.3, min_satisfaction_floor=0.0),
    )
    scores = PreferenceScorer().score(
        objective=objective,
        profiles=profiles,
        attractions=attractions,
        live_context=live,
        knowledge=InMemoryKnowledgeStore({}, corpus_version="t"),
    )
    park_graph = ParkGraph.from_sources(routing=_FlatRouting(), park=PARK, attractions=attractions)
    plan: Plan = GreedyInsertionOptimizer(park_graph=park_graph).build_plan(
        constraints=constraints,
        context=live,
        utilities=scores.utilities(),
        park=PARK,
        catalog=attractions,
        group_objective=objective,
        accessibility_reqs=accessibility,
        forecast_service=ForecastService([]),
        now=NOW,
        scores=scores,
    )
    check = ConstraintChecker().check(
        plan, constraints, accessibility, {a.node_id: a for a in attractions}, PARK, live, NOW
    )
    return constraints, plan, check


def _ride_ids(plan: Plan) -> set[str]:
    return {s.node_id for s in plan.stops if s.kind in RIDES}


def test_the_baseline_plan_includes_the_family_rides_the_avoid_test_will_exclude() -> None:
    _, plan, _ = _plan_and_check({**COMPLETE["extraction"], "avoid": []})

    assert {"id-teacups", "id-carousel"} <= _ride_ids(plan)


def test_an_avoided_attraction_is_kept_out_of_the_plan() -> None:
    constraints, plan, check = _plan_and_check(
        {**COMPLETE["extraction"], "avoid": ["Teacups", "carousel"]}
    )

    assert constraints.avoid == ["id-teacups", "id-carousel"]
    assert not {"id-teacups", "id-carousel"} & _ride_ids(plan)
    assert RuleId.AVOID not in {v.rule for v in check.violations}


def test_must_do_attractions_are_scheduled_and_pass_the_checker() -> None:
    constraints, plan, check = _plan_and_check(COMPLETE["extraction"])

    assert constraints.must_do == ["id-tron", "id-space"]
    assert {"id-tron", "id-space"} <= _ride_ids(plan)
    assert plan.unmet_must_do == []
    assert RuleId.MUST_DO not in {v.rule for v in check.violations}
