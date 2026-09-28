"""Interrupt/resume behavior of the initial planning graph.

Exercises _propose_plan and _interrupt_for_approval directly and through a
minimal compiled graph, with ProposePlanUseCase/ResolveProposalUseCase
monkeypatched so no real Postgres connection is required. The underlying
repository mechanics (activate() requiring an APPROVED proposal, etc.) are
already covered by tests/integration/postgres/test_plan_state.py; these
tests target the graph's own orchestration: node ordering, the interrupt
payload, and which state helper runs on which decision.
"""

import factories
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from parkmind.core.contracts import ApprovalStatus, RejectionReason
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph.state import ParkMindState


def _state_with_candidate() -> ParkMindState:
    return {
        "thread_id": "thread_1",
        "messages": [],
        "candidate_plan": factories.plan(),
    }


def test_propose_plan_requires_candidate():
    with pytest.raises(ValueError, match="No candidate plan"):
        ipg._propose_plan({"thread_id": "t", "messages": []})


def test_propose_plan_persists_and_sets_pending(monkeypatch):
    saved: dict = {}

    def fake_execute(self, thread_id, plan, **kwargs):
        saved["thread_id"] = thread_id
        saved["plan"] = plan
        saved["kwargs"] = kwargs
        return factories.proposal(candidate_plan_id=plan.plan_id)

    monkeypatch.setattr(ipg.ProposePlanUseCase, "execute", fake_execute)

    state = ipg._propose_plan(_state_with_candidate())

    assert saved["thread_id"] == "thread_1"
    assert saved["plan"] == state["candidate_plan"]
    assert state["proposal"].candidate_plan_id == state["candidate_plan"].plan_id
    assert state["approval"] == "PENDING"


def _compile_propose_and_interrupt_graph(saver: MemorySaver):
    graph = StateGraph(ParkMindState)
    graph.add_node("propose_plan", ipg._propose_plan)
    graph.add_node("interrupt_approval", ipg._interrupt_for_approval)
    graph.add_edge(START, "propose_plan")
    graph.add_edge("propose_plan", "interrupt_approval")
    graph.add_edge("interrupt_approval", END)
    return graph.compile(checkpointer=saver)


def test_graph_pauses_at_interrupt_with_candidate_and_proposal(monkeypatch):
    monkeypatch.setattr(
        ipg.ProposePlanUseCase,
        "execute",
        lambda self, thread_id, plan, **kw: factories.proposal(
            candidate_plan_id=plan.plan_id
        ),
    )

    compiled = _compile_propose_and_interrupt_graph(MemorySaver())
    config = {"configurable": {"thread_id": "thread_1"}}

    result = compiled.invoke(_state_with_candidate(), config=config)

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["candidate_plan"].plan_id == factories.plan().plan_id
    assert payload["proposal"].candidate_plan_id == factories.plan().plan_id

    snapshot = compiled.get_state(config)
    assert snapshot.next == ("interrupt_approval",)


def test_resuming_with_approved_activates_and_moves_candidate_to_current(monkeypatch):
    monkeypatch.setattr(
        ipg.ProposePlanUseCase,
        "execute",
        lambda self, thread_id, plan, **kw: factories.proposal(
            candidate_plan_id=plan.plan_id
        ),
    )
    resolved: dict = {}

    def fake_resolve(
        self, thread_id, proposal_id, status, *, at, rejection_reason=None
    ):
        resolved["thread_id"] = thread_id
        resolved["proposal_id"] = proposal_id
        resolved["status"] = status
        resolved["rejection_reason"] = rejection_reason
        return factories.proposal(
            proposal_id=proposal_id,
            approval_status=status,
        )

    monkeypatch.setattr(ipg.ResolveProposalUseCase, "execute", fake_resolve)

    compiled = _compile_propose_and_interrupt_graph(MemorySaver())
    config = {"configurable": {"thread_id": "thread_1"}}
    compiled.invoke(_state_with_candidate(), config=config)

    result = compiled.invoke(Command(resume={"decision": "APPROVED"}), config=config)

    assert resolved["status"] is ApprovalStatus.APPROVED
    assert resolved["rejection_reason"] is None
    assert result["current_plan"] == factories.plan()
    assert result["candidate_plan"] is None
    assert result["approval"] == "APPROVED"


def test_resuming_with_rejected_discards_candidate_without_activating(monkeypatch):
    monkeypatch.setattr(
        ipg.ProposePlanUseCase,
        "execute",
        lambda self, thread_id, plan, **kw: factories.proposal(
            candidate_plan_id=plan.plan_id
        ),
    )
    resolved: dict = {}

    def fake_resolve(
        self, thread_id, proposal_id, status, *, at, rejection_reason=None
    ):
        resolved["status"] = status
        resolved["rejection_reason"] = rejection_reason
        return factories.proposal(proposal_id=proposal_id, approval_status=status)

    monkeypatch.setattr(ipg.ResolveProposalUseCase, "execute", fake_resolve)

    compiled = _compile_propose_and_interrupt_graph(MemorySaver())
    config = {"configurable": {"thread_id": "thread_1"}}
    compiled.invoke(_state_with_candidate(), config=config)

    result = compiled.invoke(
        Command(
            resume={"decision": "REJECTED", "rejection_reason": "TOO_MUCH_WALKING"}
        ),
        config=config,
    )

    assert resolved["status"] is ApprovalStatus.REJECTED
    assert resolved["rejection_reason"] is RejectionReason.TOO_MUCH_WALKING
    assert result["candidate_plan"] is None
    assert result.get("current_plan") is None
    assert result["approval"] == "REJECTED"
    assert result["rejection_reason"] == RejectionReason.TOO_MUCH_WALKING


def test_build_initial_planning_graph_uses_checkpoint_safe_serde():
    compiled = ipg.build_initial_planning_graph(checkpointer=MemorySaver())
    assert compiled is not None
