"""Replayed approvals and repeated events change the world once; re-ask loops end."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import factories
import pytest
from elicit_support import FakeExtractor, scenario
from explain_support import NoNarrator
from langgraph.types import Command
from planning_support import NOW, factory_for

from parkmind.agents import elicit_agent, llm_budget
from parkmind.agents.elicit_agent import AnthropicExtractor, ExtractorOutputError
from parkmind.agents.llm_budget import LlmCallBudget
from parkmind.core.contracts import ApprovalStatus, EventType
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph import replanning_graph as rg
from parkmind.services.ports import RepositoryUnavailableError
from parkmind.services.use_cases.resolve_proposal import (
    ProposalNoLongerPendingError,
    ResolveProposalUseCase,
)
from resilience.res_faults import FlakyPort, Reply, ScriptedModel, run_with_deadline
from resilience.res_harness import APPROVE, CONFIG, THREAD_ID, Rig, approval_for

# --- approvals -----------------------------------------------------------------------------


def _behavior_entries(rig: Rig) -> int:
    return sum(len(entries) for entries in rig.deps.behavior.rows.values())  # type: ignore[attr-defined]


def _decide(rig: Rig, status: ApprovalStatus) -> Any:
    guests = (
        rig.graph.get_state(CONFIG, subgraphs=True)
        .tasks[0]
        .state.values["constraints"]
        .guests
    )
    return ResolveProposalUseCase(factory_for(rig.deps)).execute(
        THREAD_ID,
        rig.proposals[0].proposal_id,
        status,
        at=NOW,
        guests=guests,
    )


@pytest.mark.parametrize(
    "keyed", [False, True], ids=["bare_payload", "keyed_by_interrupt_id"]
)
def test_an_approval_replayed_after_it_was_applied_activates_the_plan_once(
    keyed: bool,
) -> None:
    rig = Rig()
    pending = rig.confirm()["__interrupt__"][0]
    payload = approval_for(pending.id) if keyed else APPROVE
    rig.invoke(payload)
    active, entries = rig.active, _behavior_entries(rig)

    rig.invoke(payload)
    rig.invoke(payload)

    assert rig.active == active
    assert [p.approval_status for p in rig.proposals] == [ApprovalStatus.APPROVED]
    assert _behavior_entries(rig) == entries


def test_the_decision_use_case_refuses_the_same_approval_twice() -> None:
    rig = Rig()
    rig.confirm()
    guests = rig.state()["constraints"].guests
    resolve = ResolveProposalUseCase(factory_for(rig.deps))
    proposal_id = rig.proposals[0].proposal_id
    resolve.execute(
        THREAD_ID, proposal_id, ApprovalStatus.APPROVED, at=NOW, guests=guests
    )
    active, entries = rig.active, _behavior_entries(rig)

    with pytest.raises(ProposalNoLongerPendingError) as replay:
        resolve.execute(
            THREAD_ID, proposal_id, ApprovalStatus.APPROVED, at=NOW, guests=guests
        )

    assert replay.value.status is ApprovalStatus.APPROVED
    assert rig.active == active
    assert _behavior_entries(rig) == entries == len(guests)


@pytest.mark.parametrize("first", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
def test_a_conflicting_second_decision_changes_nothing(first: ApprovalStatus) -> None:
    rig = Rig()
    rig.confirm()
    guests = rig.state()["constraints"].guests
    resolve = ResolveProposalUseCase(factory_for(rig.deps))
    proposal_id = rig.proposals[0].proposal_id
    second = (
        ApprovalStatus.REJECTED
        if first is ApprovalStatus.APPROVED
        else ApprovalStatus.APPROVED
    )
    resolve.execute(THREAD_ID, proposal_id, first, at=NOW, guests=guests)
    active, entries = rig.active, _behavior_entries(rig)

    with pytest.raises(ProposalNoLongerPendingError):
        resolve.execute(THREAD_ID, proposal_id, second, at=NOW, guests=guests)

    assert rig.active == active
    assert rig.proposals[0].approval_status is first
    assert _behavior_entries(rig) == entries


# --- replanning events ---------------------------------------------------------------------


class _Replanning:
    """An approved initial plan, and a replanning graph on the same thread."""

    def __init__(self, rig: Rig) -> None:
        rig.confirm()
        rig.invoke(APPROVE)
        self.rig = rig
        self.graph = self._build()

    def _build(self) -> Any:
        return rg.build_replanning_graph(
            self.rig.checkpointer,
            deps_factory=factory_for(self.rig.deps),
            clock=lambda: NOW,
            narrator=NoNarrator(),
        )

    def swap(self, **ports: Any) -> None:
        self.rig.deps = replace(self.rig.deps, **ports)
        self.graph = self._build()

    def send(self, *events: Any) -> Any:
        payload = {"thread_id": THREAD_ID, "events": list(events)}
        return run_with_deadline(lambda: self.graph.invoke(payload, config=CONFIG))

    def closed_stop(self) -> str:
        return str(self.rig.active.stops[0].node_id)

    def down(self, event_id: str = "ev_down", **overrides: Any) -> Any:
        return factories.event(
            **{
                "event_id": event_id,
                "type": EventType.ATTRACTION_DOWN,
                "attraction_id": self.closed_stop(),
                "timestamp": NOW + timedelta(minutes=1),
                **overrides,
            }
        )

    @property
    def pending(self) -> list[Any]:
        return [
            p for p in self.rig.proposals if p.approval_status is ApprovalStatus.PENDING
        ]

    @property
    def recorded(self) -> list[Any]:
        return [
            e
            for e in self.rig.deps.events.list_for_thread(THREAD_ID)
            if e.type is EventType.ATTRACTION_DOWN
        ]


@pytest.fixture
def replanning() -> _Replanning:
    return _Replanning(Rig())


def test_the_same_event_sent_while_its_replan_waits_does_not_add_a_proposal(
    replanning: _Replanning,
) -> None:
    event = replanning.down()
    replanning.send(event)
    waiting = [p.proposal_id for p in replanning.pending]

    replanning.send(event)

    assert [p.proposal_id for p in replanning.pending] == waiting
    assert len(replanning.rig.proposals) == 2
    assert len(replanning.recorded) == 1


def test_the_same_event_twice_in_one_batch_replans_once(
    replanning: _Replanning,
) -> None:
    event = replanning.down()

    replanning.send(event, event)

    assert len(replanning.rig.proposals) == 2
    assert len(replanning.pending) == 1
    assert len(replanning.recorded) == 1


def test_the_same_closure_reported_under_another_event_id_replans_once(
    replanning: _Replanning,
) -> None:
    replanning.send(replanning.down("ev_first"))
    waiting = [p.proposal_id for p in replanning.pending]

    replanning.send(replanning.down("ev_second", timestamp=NOW + timedelta(minutes=2)))

    assert [p.proposal_id for p in replanning.pending] == waiting
    assert len(replanning.rig.proposals) == 2
    assert len(replanning.recorded) == 1


def test_the_same_event_after_its_replan_was_approved_proposes_nothing_new(
    replanning: _Replanning,
) -> None:
    event = replanning.down()
    replanning.send(event)
    run_with_deadline(lambda: replanning.graph.invoke(APPROVE, config=CONFIG))
    active, proposals = replanning.rig.active, len(replanning.rig.proposals)

    result = replanning.send(event)

    assert "__interrupt__" not in result
    assert replanning.rig.active == active
    assert len(replanning.rig.proposals) == proposals


def test_an_event_whose_replan_failed_replans_when_it_is_sent_again(
    replanning: _Replanning,
) -> None:
    snapshots = FlakyPort(
        replanning.rig.deps.snapshots,
        RepositoryUnavailableError("snapshot store down"),
        failures=1,
        methods={"recent_ids"},
    )
    replanning.swap(snapshots=snapshots)
    event = replanning.down()
    failed = replanning.send(event)
    assert "__interrupt__" not in failed
    assert len(replanning.pending) == 0

    replanning.send(event)

    assert len(replanning.pending) == 1


# --- loops that ask again ------------------------------------------------------------------


def test_an_extractor_that_keeps_returning_bad_output_is_called_a_fixed_number_of_times_per_turn() -> (
    None
):
    extractor = FakeExtractor(ExtractorOutputError("no structured output"))
    rig = Rig(extractor=extractor)
    rig.start()
    assert len(extractor.calls) == elicit_agent.DEFAULT_MAX_ATTEMPTS

    for turn in range(2, 6):
        result = rig.invoke(Command(resume="I said what I said"))
        assert result["__interrupt__"][0].value["missing"] == ["unreadable_response"]
        assert len(extractor.calls) == turn * elicit_agent.DEFAULT_MAX_ATTEMPTS

    assert rig.proposals == []
    assert rig.active is None


def test_an_extractor_that_keeps_returning_incomplete_data_is_called_once_per_turn() -> (
    None
):
    extractor = FakeExtractor(scenario("wiki_example_missing_departure")["extraction"])
    rig = Rig(extractor=extractor)
    rig.start()

    for turn in range(2, 6):
        result = rig.invoke(Command(resume="I do not know"))
        assert result["__interrupt__"][0].value["missing"] == ["departure_time"]
        assert len(extractor.calls) == turn

    assert rig.proposals == []


def test_a_spent_call_budget_stops_the_model_calls_and_the_graph_keeps_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_budget.time, "sleep", lambda _seconds: None)
    model = ScriptedModel(
        Reply(
            [{"args": dict(scenario("wiki_example_missing_departure")["extraction"])}]
        )
    )
    extractor = AnthropicExtractor(api_key="test-key", budget=LlmCallBudget(3))
    extractor._llm = model
    rig = Rig(extractor=extractor)
    rig.start()

    for _ in range(6):
        result = rig.invoke(Command(resume="I do not know"))
        assert "__interrupt__" in result

    assert model.calls == 3
    assert rig.proposals == []
    assert rig.active is None


def test_the_approval_prompt_is_asked_again_for_an_invalid_answer_without_side_effects() -> (
    None
):
    rig = Rig()
    rig.confirm()

    for _ in range(4):
        result = rig.invoke(Command(resume={"decision": "MAYBE"}))
        assert result["__interrupt__"][0].value["kind"] == ipg.APPROVAL_INTERRUPT_KIND
        assert rig.active is None
        assert [p.approval_status for p in rig.proposals] == [ApprovalStatus.PENDING]
