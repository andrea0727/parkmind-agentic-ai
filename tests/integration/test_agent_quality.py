"""
Agent quality checks — the 4 checks scoped for this project (see the wiki,
"Quality & Testing" page, for the realistic-scope rationale).

We deliberately do NOT build the full QA pipeline (DeepEval/Ragas scoring,
garak/PyRIT adversarial/prompt-injection testing, CI regression suites).
With one QA-capable person on a 3-person team in a 1-month build, these
four checks are the representative set that catches the failure modes that
actually matter: an agent that invents facts, an agent that silently drops
a stated constraint, an agent that leaks one guest's data into another's
session, and an agent that ignores the guest model it's supposedly built
around.

Each runs on the in-memory ports with scripted LLM stand-ins: no network, no
database, no API key. Keep them this simple; they're not meant to grow into a
framework.

Mapping, so it's explicit where each concern lives:

  - "Do the agents/nodes communicate correctly?" -> NOT here. That's
    exactly what test_initial_planning_graph.py and
    test_replanning_graph.py already check.
  - "Do they save/keep memory correctly?" -> TestMemorySessionIsolation.
  - "Is the LLM output grounded / not hallucinating?" -> TestHallucinationDetection.
  - "Does the extraction keep the hard constraints?" -> TestBusinessRuleValidation.
  - "Does the plan actually reflect the guest?" -> TestGuestAlignment.
"""

from datetime import timedelta
from typing import Any

import factories
from elicit_support import FakeExtractor, make_intake, make_names, scenario
from explain_support import NoNarrator, ScriptedNarrator
from fakes import InMemorySnapshotRepository
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from planning_support import (
    NOW,
    factory_for,
    make_deps,
    seed_snapshot,
    snapshot_context,
)

from parkmind.agents.elicit_agent import build_party_constraints
from parkmind.core.contracts import (
    AttractionCategory,
    PreferenceSource,
    SensitivityKind,
    SensitivityLevel,
    StopKind,
)
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.services.use_cases.build_plan import BuildPlanUseCase

COMPLETE = scenario("wiki_example_complete")
WALKING_BUDGET = scenario("party_walking_budget")


def _graph(
    extraction: Any,
    *,
    narrator: Any = None,
    checkpointer: Any = None,
) -> Any:
    intake, sessions = make_intake()
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context(retrieved_at=NOW - timedelta(minutes=5)))
    deps = make_deps(sessions=sessions, snapshots=snapshots)
    return ipg.build_initial_planning_graph(
        FakeExtractor(extraction),
        intake,
        checkpointer or default_checkpointer(),
        deps_factory=factory_for(deps),
        clock=lambda: NOW,
        names=make_names(),
        narrator=narrator or NoNarrator(),
    )


def _config(thread_id: str) -> Any:
    return {"configurable": {"thread_id": thread_id}}


def _start(graph: Any, thread_id: str, messages: list[str]) -> Any:
    return graph.invoke(
        {
            "thread_id": thread_id,
            "messages": [HumanMessage(content=m) for m in messages],
        },
        config=_config(thread_id),
    )


def _confirm(graph: Any, thread_id: str) -> Any:
    return graph.invoke(
        Command(resume={"confirmed": True, "consent": True}),
        config=_config(thread_id),
    )


def _values(graph: Any, thread_id: str) -> dict[str, Any]:
    """The thread's values; while paused inside the planning subgraph, that subgraph's."""
    snapshot = graph.get_state(_config(thread_id), subgraphs=True)
    for task in snapshot.tasks:
        if task.state is not None and hasattr(task.state, "values"):
            return task.state.values
    return snapshot.values


def _planned(graph: Any, thread_id: str = "t1") -> dict[str, Any]:
    _start(graph, thread_id, COMPLETE["messages"])
    _confirm(graph, thread_id)
    return _values(graph, thread_id)


class TestHallucinationDetection:
    """Groundedness: every number in an explanation must exist in the plan's data
    (the payload the narrator was given). An invented wait or walking time makes
    EXPLAIN discard the narrator's text and fall back to the template, which is
    built from the plan alone.

    A plain number match is enough for the MVP; DeepEval's FaithfulnessMetric
    would be a drop-in upgrade, not a requirement.
    """

    def test_explanation_only_cites_grounded_facts(self) -> None:
        invented = "Space Mountain has a 47 minute wait and TRON is a 93 minute walk."
        narrator = ScriptedNarrator(invented, invented)
        graph = _graph(COMPLETE["extraction"], narrator=narrator)

        state = _planned(graph)

        assert len(narrator.payloads) == 2  # one repair attempt, then the template
        assert "47" not in state["explanation"] and "93" not in state["explanation"]
        assert state["explanation"]
        versions = state["candidate_plan"].provenance.prompt_versions
        assert versions["explain"] == "template"

    def test_a_grounded_explanation_is_kept(self) -> None:
        text = "Your day starts with TRON and Space Mountain, with lunch in between."
        narrator = ScriptedNarrator(text)
        graph = _graph(COMPLETE["extraction"], narrator=narrator)

        state = _planned(graph)

        assert state["explanation"] == text
        assert (
            state["candidate_plan"].provenance.prompt_versions["explain"] != "template"
        )


class TestBusinessRuleValidation:
    """The agent's constraint extraction must not silently invent or drop
    hard constraints. If the guest mentions a walking limit, the extracted
    output must contain it — regardless of whether the final plan happens
    to be fine anyway."""

    def test_stated_accessibility_constraint_is_extracted(self) -> None:
        graph = _graph(WALKING_BUDGET["extraction"])

        paused = _start(graph, "t1", WALKING_BUDGET["messages"])

        # Stated by the guests, so it waits for their confirmation as a hard item...
        pending = paused["__interrupt__"][0].value
        assert "walking_budget: 180" in str(pending)

        # ...and once confirmed it reaches the constraints the planner runs on.
        _confirm(graph, "t1")
        state = _values(graph, "t1")
        assert state["constraints"].party_walking_budget_minutes == 180

    def test_the_budget_is_in_the_contract_built_from_the_extraction(self) -> None:
        from parkmind.agents.elicit_schema import ElicitationExtraction

        extraction = ElicitationExtraction.model_validate(WALKING_BUDGET["extraction"])
        constraints = build_party_constraints(
            extraction,
            today=NOW.date(),
            resolution=make_names().resolve(extraction.must_do),
        )

        assert constraints.party_walking_budget_minutes == 180


class TestMemorySessionIsolation:
    """Two different thread_ids must never see each other's state — the
    cheapest thing to get catastrophically wrong with LangGraph
    checkpointing."""

    def test_two_threads_do_not_share_state(self) -> None:
        checkpointer = default_checkpointer()
        graph = _graph(COMPLETE["extraction"], checkpointer=checkpointer)

        _planned(graph, "family-a")
        # A second party on the same graph and checkpointer, still at the first message.
        _start(graph, "family-b", ["hello"])

        a, b = _values(graph, "family-a"), _values(graph, "family-b")
        assert a["thread_id"] == "family-a" and b["thread_id"] == "family-b"
        assert a["candidate_plan"] is not None
        assert b.get("candidate_plan") is None
        assert b.get("current_plan") is None
        assert [m.content for m in b["messages"] if m.type == "human"] == ["hello"]
        assert "hello" not in [m.content for m in a["messages"]]


class TestGuestAlignment:
    """Does the plan actually reflect the guest's stated preferences? The
    cheapest version of Personalization Lift, applied to one profile."""

    def test_low_intensity_preference_avoids_high_intensity_stops(self) -> None:
        constraints = factories.party_constraints(
            departure_time=NOW + timedelta(hours=2),
        )
        profile = factories.guest_profile(
            sensitivities={SensitivityKind.INTENSITY: SensitivityLevel.HIGH},
            avoided_categories=[AttractionCategory.THRILL],
            preferred_categories=[AttractionCategory.FAMILY],
            queue_tolerance=factories.preference(0.5, source=PreferenceSource.STATED),
        )
        deps = make_deps()
        use_case = BuildPlanUseCase(factory_for(deps))
        live = snapshot_context()
        objective = use_case.resolve_group(
            thread_id="t1",
            constraints=constraints,
            profiles=[profile],
            accessibility_ref=[],
            live_context=live,
        )

        built = use_case.build(
            thread_id="t1",
            constraints=constraints,
            profiles=[profile],
            accessibility_ref=[],
            live_context=live,
            objective=objective,
            now=NOW,
        )

        thrill_ids = {"id-tron", "id-space", "id-coaster"}
        rides = [s.node_id for s in built.plan.stops if s.kind == StopKind.ATTRACTION]
        assert rides, "the plan should still have something to do"
        assert not thrill_ids & set(rides)
