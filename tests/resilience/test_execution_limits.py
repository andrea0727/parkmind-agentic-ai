"""Runaway flows stop at the step limit with a typed error, and a stuck call is caught by the deadline."""

import threading
from typing import Any, TypedDict

import pytest
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, StateGraph

from resilience.res_faults import run_with_deadline
from resilience.res_harness import COMPLETE, THREAD_ID, Rig


class _Loop(TypedDict):
    steps: int


def _endless_graph() -> Any:
    def spin(state: _Loop) -> dict[str, int]:
        return {"steps": state["steps"] + 1}

    builder = StateGraph(_Loop)
    builder.add_node("spin", spin)
    builder.add_edge(START, "spin")
    builder.add_conditional_edges("spin", lambda state: "spin")
    return builder.compile()


def _limited(limit: int) -> Any:
    return {"configurable": {"thread_id": THREAD_ID}, "recursion_limit": limit}


@pytest.mark.parametrize("limit", [1, 3, 25])
def test_a_flow_that_never_ends_raises_a_recursion_error_at_the_limit(
    limit: int,
) -> None:
    graph = _endless_graph()

    with pytest.raises(GraphRecursionError):
        run_with_deadline(lambda: graph.invoke({"steps": 0}, config=_limited(limit)))


def test_the_step_limit_bounds_the_work_done_before_stopping() -> None:
    seen: list[int] = []

    def spin(state: _Loop) -> dict[str, int]:
        seen.append(state["steps"])
        return {"steps": state["steps"] + 1}

    builder = StateGraph(_Loop)
    builder.add_node("spin", spin)
    builder.add_edge(START, "spin")
    builder.add_conditional_edges("spin", lambda state: "spin")
    graph = builder.compile()

    with pytest.raises(GraphRecursionError):
        run_with_deadline(lambda: graph.invoke({"steps": 0}, config=_limited(5)))

    assert len(seen) <= 5


def test_the_planning_graph_stops_with_a_recursion_error_when_given_too_few_steps() -> (
    None
):
    rig = Rig()
    payload = {
        "thread_id": THREAD_ID,
        "messages": [HumanMessage(content=m) for m in COMPLETE["messages"]],
    }

    with pytest.raises(GraphRecursionError):
        rig.invoke(payload, config=_limited(1))

    assert rig.proposals == []
    assert rig.active is None


def test_the_planning_graph_with_the_default_limit_reaches_its_first_question() -> None:
    rig = Rig()

    result = rig.start()

    assert result["__interrupt__"][0].value["kind"] == "hard_constraint_confirmation"


def test_a_call_that_never_returns_fails_the_test_instead_of_hanging_it() -> None:
    release = threading.Event()

    with pytest.raises(pytest.fail.Exception, match="stuck"):
        run_with_deadline(release.wait, seconds=0.2)

    release.set()


def test_a_call_that_returns_in_time_gives_its_value_and_its_errors_back() -> None:
    assert run_with_deadline(lambda: 7, seconds=5) == 7

    def broken() -> None:
        raise KeyError("boom")

    with pytest.raises(KeyError):
        run_with_deadline(broken, seconds=5)
