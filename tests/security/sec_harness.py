"""An offline initial-planning graph with scripted fakes, for the security tests."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import factories  # noqa: F401
from elicit_support import FakeExtractor, FakeSessionStore, make_intake, make_names
from explain_support import NoNarrator
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

from parkmind.agents.explain_agent import NarratorUnavailableError
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.services.use_cases.planning_deps import PlanningDeps

BENIGN_MESSAGE = "Two adults, we want TRON, we leave the park at 8 PM."
BENIGN_EXTRACTION: dict[str, Any] = {
    "party_size": 2,
    "guests": [{"role": "adult"}, {"role": "adult"}],
    "must_do": ["TRON"],
    "departure_time": "20:00",
}
CHILDREN_ONLY_MESSAGE = "Two kids, 9 and 11, want TRON. We leave the park at 8 PM."
CHILDREN_ONLY_EXTRACTION: dict[str, Any] = {
    "party_size": 2,
    "guests": [{"role": "child"}, {"role": "child"}],
    "must_do": ["TRON"],
    "departure_time": "20:00",
}


class RecordingNarrator:
    """Records every payload and hint it receives, then declines so the template is used."""

    def __init__(self) -> None:
        self.payloads: list[str] = []
        self.hints: list[str | None] = []

    def narrate(
        self, payload_json: str, repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        self.payloads.append(payload_json)
        self.hints.append(repair_hint)
        raise NarratorUnavailableError("recorded")


@dataclass
class Rig:
    graph: Any
    extractor: Any
    narrator: Any
    deps: PlanningDeps
    store: FakeSessionStore
    intake: Any
    thread_id: str = "t-sec"
    seen: list[Any] = field(default_factory=list)

    def config(self, thread_id: str | None = None) -> Any:
        return {"configurable": {"thread_id": thread_id or self.thread_id}}

    def start(self, *messages: str, thread_id: str | None = None) -> dict[str, Any]:
        tid = thread_id or self.thread_id
        result = self.graph.invoke(
            {"thread_id": tid, "messages": [HumanMessage(content=m) for m in messages]},
            config=self.config(tid),
        )
        self.seen.append(result)
        return result

    def resume(self, value: Any, thread_id: str | None = None) -> dict[str, Any]:
        tid = thread_id or self.thread_id
        result = self.graph.invoke(Command(resume=value), config=self.config(tid))
        self.seen.append(result)
        return result

    def confirm(self, thread_id: str | None = None, **extra: Any) -> dict[str, Any]:
        return self.resume({"confirmed": True, "consent": True, **extra}, thread_id)

    def approve(self, thread_id: str | None = None) -> dict[str, Any]:
        return self.resume({"decision": "APPROVED"}, thread_id)

    def values(self, thread_id: str | None = None) -> dict[str, Any]:
        snapshot = self.graph.get_state(self.config(thread_id), subgraphs=True)
        for task in snapshot.tasks:
            if task.state is not None and hasattr(task.state, "values"):
                return dict(task.state.values)
        return dict(snapshot.values)

    def active_plan(self, thread_id: str | None = None) -> Any:
        return self.deps.plans.get_active(thread_id or self.thread_id)

    def proposals(self) -> list[Any]:
        return [proposal for _, proposal in self.deps.proposals.rows.values()]


def interrupt_of(result: Mapping[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__")
    return dict(interrupts[0].value) if interrupts else None


def kind_of(result: Mapping[str, Any]) -> str | None:
    payload = interrupt_of(result)
    return None if payload is None else str(payload["kind"])


def make_rig(
    *extractions: Any,
    narrator: Any = None,
    thread_id: str = "t-sec",
    extractor: Any = None,
) -> Rig:
    intake, store = make_intake()
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context(retrieved_at=NOW - timedelta(minutes=5)))
    deps = make_deps(sessions=store, snapshots=snapshots)
    extractor = extractor or FakeExtractor(*extractions)
    narrator = narrator or NoNarrator()
    graph = ipg.build_initial_planning_graph(
        extractor,
        intake,
        default_checkpointer(),
        deps_factory=factory_for(deps),
        clock=lambda: NOW,
        names=make_names(),
        narrator=narrator,
    )
    return Rig(graph, extractor, narrator, deps, store, intake, thread_id)


MISSING = "missing_information"
CONFIRMATION = "hard_constraint_confirmation"
APPROVAL = "plan_approval"

HOSTILE_MARKERS: tuple[str, ...] = (
    "ignore",
    "instruction",
    "drop table",
    "system prompt",
    "api key",
    "evil",
    "approve",
    "activate",
    "<script",
    "jndi",
    "passwd",
)


def hostile_markers_in(text: str) -> list[str]:
    lowered = text.lower()
    return [marker for marker in HOSTILE_MARKERS if marker in lowered]


def assert_not_planned(rig: Rig, thread_id: str | None = None) -> None:
    values = rig.values(thread_id)
    for key in (
        "candidate_plan",
        "current_plan",
        "check_result",
        "explanation",
        "proposal",
    ):
        assert values.get(key) is None, f"{key} was set before confirmation"
    assert values.get("approval") in (None, "PENDING")
    assert values.get("constraints_valid") is not True
    assert rig.proposals() == []
    assert rig.active_plan(thread_id) is None


def assert_not_activated(rig: Rig, thread_id: str | None = None) -> None:
    assert rig.values(thread_id).get("current_plan") is None
    assert rig.active_plan(thread_id) is None
    assert all(p.approval_status.value == "PENDING" for p in rig.proposals())
