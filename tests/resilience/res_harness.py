"""A planning graph on in-memory ports that tests can break, rebuild and resume."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any

from elicit_support import FakeExtractor, FakeSessionStore, make_names, scenario
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

from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.services.use_cases.accessibility_intake import AccessibilityIntakeUseCase
from parkmind.services.use_cases.planning_deps import PlanningDeps
from resilience.res_faults import run_with_deadline

COMPLETE = scenario("wiki_example_complete")
THREAD_ID = "t1"
CONFIG: Any = {"configurable": {"thread_id": THREAD_ID}}
APPROVE = Command(resume={"decision": "APPROVED"})
CONFIRM = Command(resume={"confirmed": True, "consent": True})


def approval_for(interrupt_id: str) -> Command:
    """An approval keyed by interrupt id: a value kept from a failed run cannot answer a later interrupt."""
    return Command(resume={interrupt_id: {"decision": "APPROVED"}})


def intake_over(sessions: FakeSessionStore) -> AccessibilityIntakeUseCase:
    """An intake use case writing to ``sessions``: a restarted process reopens the same store."""

    @contextmanager
    def factory() -> Iterator[FakeSessionStore]:
        yield sessions

    return AccessibilityIntakeUseCase(store_factory=factory)


class Rig:
    """Graph, ports and checkpointer; every graph call is bounded by a wall-clock deadline."""

    def __init__(
        self,
        *,
        extractor: Any = None,
        collector: Any = None,
        snapshot: bool = True,
        snapshot_age: timedelta = timedelta(0),
    ) -> None:
        self.sessions = FakeSessionStore()
        snapshots = InMemorySnapshotRepository()
        if snapshot:
            seed_snapshot(
                snapshots,
                snapshot_context(
                    retrieved_at=NOW - snapshot_age - timedelta(minutes=5)
                ),
            )
        self.deps: PlanningDeps = make_deps(
            sessions=self.sessions, snapshots=snapshots, collector=collector
        )
        self.checkpointer = default_checkpointer()
        self.extractor = extractor or FakeExtractor(COMPLETE["extraction"])
        self.graph = self.build()

    def build(self) -> Any:
        """A new graph object over the same ports and checkpointer, as after a restart."""
        return ipg.build_initial_planning_graph(
            self.extractor,
            intake_over(self.sessions),
            self.checkpointer,
            deps_factory=factory_for(self.deps),
            clock=lambda: NOW,
            names=make_names(),
            narrator=NoNarrator(),
        )

    def swap(self, **ports: Any) -> None:
        """Replace ports on the deps and rebuild the graph over them."""
        self.deps = replace(self.deps, **ports)
        self.graph = self.build()

    def invoke(self, payload: Any, config: Any = CONFIG) -> Any:
        return run_with_deadline(lambda: self.graph.invoke(payload, config=config))

    def start(self) -> Any:
        return self.invoke(
            {
                "thread_id": THREAD_ID,
                "messages": [HumanMessage(content=m) for m in COMPLETE["messages"]],
            }
        )

    def confirm(self) -> Any:
        self.start()
        return self.invoke(CONFIRM)

    def state(self) -> dict[str, Any]:
        """The thread's values; while paused inside the planning subgraph, that subgraph's."""
        snapshot = self.graph.get_state(CONFIG, subgraphs=True)
        for task in snapshot.tasks:
            if task.state is not None and hasattr(task.state, "values"):
                return task.state.values
        return snapshot.values

    @property
    def proposals(self) -> list[Any]:
        return [p for _, p in self.deps.proposals.rows.values()]  # type: ignore[attr-defined]

    @property
    def active(self) -> Any:
        return self.deps.plans.get_active(THREAD_ID)
