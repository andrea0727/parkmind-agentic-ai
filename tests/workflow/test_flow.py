"""Layer 2: routing, state and interrupt/resume behavior of the initial planning graph."""

from typing import Any

import pytest
from e2e import catalog
from e2e.kit.driver import run_scenario
from e2e.kit.verdicts import FULL
from e2e.model import Scenario
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from workflow.runtime import OfflineRuntime

HAPPY = catalog.by_id("happy_bd_poblada")
CONFIG: Any = {"configurable": {"thread_id": "flow"}}


def _start(scenario: Scenario) -> tuple[Any, Any]:
    built = OfflineRuntime().build(scenario)
    result = built.graph.invoke(
        {
            "thread_id": "flow",
            "messages": [HumanMessage(content=m) for m in scenario.texts()],
        },
        config=CONFIG,
    )
    return built, result


def _kind(result: Any) -> str | None:
    interrupts = result.get("__interrupt__")
    return interrupts[0].value["kind"] if interrupts else None


def test_the_planning_stages_run_once_each_and_in_order() -> None:
    facts = run_scenario(HAPPY, OfflineRuntime())

    assert facts["stages_elicit"] == []
    assert facts["stages"] == FULL
    assert facts["stages_after_approval"] == ["resolve_proposal"]


def test_the_graph_pauses_three_times_in_the_documented_order() -> None:
    facts = run_scenario(HAPPY, OfflineRuntime())

    assert facts["interrupt1"]["kind"] == "hard_constraint_confirmation"
    assert facts["interrupt2"]["kind"] == "plan_approval"
    assert facts["interrupt3"] is None


def test_nothing_is_active_until_the_guests_approve() -> None:
    built, first = _start(HAPPY)
    assert _kind(first) == "hard_constraint_confirmation"

    second = built.graph.invoke(Command(resume=dict(HAPPY.confirm)), config=CONFIG)

    assert _kind(second) == "plan_approval"
    assert built.graph.get_state(CONFIG, subgraphs=True).tasks
    assert second["__interrupt__"][0].value["candidate_plan"] is not None


def test_a_rebuilt_graph_resumes_the_same_thread_from_the_checkpoint() -> None:
    built, first = _start(HAPPY)
    assert _kind(first) == "hard_constraint_confirmation"
    rebuilt = OfflineRuntime().build(HAPPY)
    rebuilt.graph.checkpointer = built.graph.checkpointer

    resumed = rebuilt.graph.invoke(Command(resume=dict(HAPPY.confirm)), config=CONFIG)

    assert _kind(resumed) == "plan_approval"


@pytest.mark.parametrize("bad", ["yes", 42, {"confirmed": "maybe"}])
def test_a_malformed_confirmation_never_releases_planning(bad: Any) -> None:
    built, first = _start(HAPPY)
    assert _kind(first) == "hard_constraint_confirmation"

    result = built.graph.invoke(Command(resume=bad), config=CONFIG)

    assert _kind(result) != "plan_approval"
    assert built.graph.get_state(CONFIG).values.get("current_plan") is None


def test_a_rejected_replan_leaves_the_approved_plan_active() -> None:
    facts = run_scenario(
        catalog.by_id("opcion_cerrada_alternativa_rechazada"), OfflineRuntime()
    )

    assert facts["replan"]["proposed"]
    assert facts["replan"]["active_unchanged"]
    assert not facts["replan"]["new_plan_active"]


def test_an_approved_replan_never_keeps_the_closed_ride() -> None:
    facts = run_scenario(catalog.by_id("opcion_cerrada_alternativa"), OfflineRuntime())

    assert facts["replan"]["candidate_avoids_closed"]
    assert facts["replan"]["new_plan_active"]
    assert not facts["replan"]["closed_in_active_plan"]
