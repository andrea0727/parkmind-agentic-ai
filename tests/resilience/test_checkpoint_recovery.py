"""A process that stops and restarts picks the thread up from its checkpoint, on the same ports."""

from elicit_support import FakeExtractor
from langchain_core.messages import HumanMessage

from parkmind.core.contracts import ApprovalStatus
from resilience.res_harness import (
    APPROVE,
    COMPLETE,
    CONFIG,
    CONFIRM,
    THREAD_ID,
    Rig,
    approval_for,
)


def _restart(rig: Rig) -> None:
    """A new graph object on the same checkpointer, and an extractor that must stay unused."""
    rig.extractor = FakeExtractor(
        RuntimeError("the restarted graph must not ask the model again")
    )
    rig.graph = rig.build()


def test_a_restarted_graph_resumes_at_the_approval_and_activates_the_same_plan() -> (
    None
):
    rig = Rig()
    rig.confirm()
    candidate = rig.state()["candidate_plan"]
    asked = len(rig.extractor.calls)
    first_graph = rig.graph
    _restart(rig)

    result = rig.invoke(APPROVE)

    assert rig.graph is not first_graph
    assert "__interrupt__" not in result
    assert rig.active.plan_id == candidate.plan_id
    assert [p.approval_status for p in rig.proposals] == [ApprovalStatus.APPROVED]
    assert len(rig.extractor.calls) == 0
    assert asked == 1


def test_a_restarted_graph_still_knows_the_question_it_was_waiting_on() -> None:
    rig = Rig()
    pending = rig.confirm()["__interrupt__"][0]
    _restart(rig)

    waiting = rig.graph.get_state(CONFIG, subgraphs=True)

    assert [i.id for task in waiting.tasks for i in task.interrupts] == [pending.id]
    assert rig.invoke(approval_for(pending.id))["messages"]
    assert rig.active is not None


def test_a_restart_before_confirmation_asks_again_rather_than_plan_without_the_flags() -> (
    None
):
    rig = Rig()
    rig.start()
    rig.graph = rig.build()

    again = rig.invoke(CONFIRM)

    assert again["__interrupt__"][0].value["kind"] == "hard_constraint_confirmation"
    assert rig.proposals == []
    assert rig.active is None
    assert len(rig.extractor.calls) == 2

    result = rig.invoke(CONFIRM)

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert len(rig.proposals) == 1
    rig.graph = rig.build()
    rig.invoke(APPROVE)
    assert rig.active is not None


def test_a_restart_after_the_plan_was_activated_leaves_it_alone() -> None:
    rig = Rig()
    rig.confirm()
    rig.invoke(APPROVE)
    active = rig.active
    _restart(rig)

    rig.invoke(APPROVE)

    assert rig.active == active
    assert [p.approval_status for p in rig.proposals] == [ApprovalStatus.APPROVED]


def test_a_second_thread_on_the_restarted_graph_starts_clean() -> None:
    rig = Rig()
    rig.confirm()
    _restart(rig)
    rig.extractor = FakeExtractor(COMPLETE["extraction"])
    rig.graph = rig.build()
    other: dict = {"configurable": {"thread_id": "t2"}}

    result = rig.invoke(
        {
            "thread_id": "t2",
            "messages": [HumanMessage(content=m) for m in COMPLETE["messages"]],
        },
        config=other,
    )

    assert result["__interrupt__"][0].value["kind"] == "hard_constraint_confirmation"
    assert rig.deps.plans.get_active("t2") is None
    assert rig.deps.plans.get_active(THREAD_ID) is None
    assert [p.approval_status for p in rig.proposals] == [ApprovalStatus.PENDING]
