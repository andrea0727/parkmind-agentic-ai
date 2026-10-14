"""A ConversationService over the in-memory ports, for the service and API tests."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from elicit_support import FakeExtractor, make_intake, make_names, scenario
from fakes import InMemorySnapshotRepository
from planning_support import (
    NOW,
    factory_for,
    make_deps,
    seed_snapshot,
    snapshot_context,
)

from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph import replanning_graph as rg
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.conversation import ConversationService
from parkmind.services.use_cases.accessibility_intake import AccessibilityIntakeUseCase
from parkmind.services.use_cases.planning_deps import PlanningDeps

COMPLETE = scenario("wiki_example_complete")
MISSING = scenario("wiki_example_missing_departure")
FIRST_REQUEST = " ".join(COMPLETE["messages"])


@dataclass
class Rig:
    service: ConversationService
    deps: PlanningDeps
    extractor: FakeExtractor
    intake: AccessibilityIntakeUseCase


def make_rig(*extractions: Any, clock: Callable[[], datetime] | None = None) -> Rig:
    """``clock`` replaces the fixed ``NOW`` in the graphs and the service."""
    now = clock or (lambda: NOW)
    intake, sessions = make_intake()
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(snapshots, snapshot_context(retrieved_at=NOW - timedelta(minutes=5)))
    deps = make_deps(sessions=sessions, snapshots=snapshots)
    checkpointer = default_checkpointer()
    extractor = FakeExtractor(*(extractions or (COMPLETE["extraction"],)))
    initial = ipg.build_initial_planning_graph(
        extractor,
        intake,
        checkpointer,
        deps_factory=factory_for(deps),
        clock=now,
        names=make_names(),
    )
    replanning = rg.build_replanning_graph(
        checkpointer, deps_factory=factory_for(deps), clock=now
    )
    service = ConversationService(
        initial,
        replanning,
        deps_factory=factory_for(deps),
        clock=now,
    )
    return Rig(service, deps, extractor, intake)
