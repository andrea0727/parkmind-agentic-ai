"""The offline runtime: the real graphs on in-memory ports with a canned extraction.

No network, database or LLM: the scenario's ``offline`` spec says what a correct extractor
returns and which collaborators are down, and the scenario's own judge decides the outcome.
"""

from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timedelta
from typing import Any

from e2e.kit.driver import Built
from e2e.kit.recorder import RecordingExtractor
from e2e.model import Scenario
from elicit_support import FakeExtractor, catalog, make_intake, make_names
from explain_support import NoNarrator
from fakes import InMemorySnapshotRepository
from planning_support import (
    NOW,
    FakeCollector,
    factory_for,
    make_deps,
    seed_snapshot,
    snapshot_context,
)

from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph import replanning_graph as rg
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.services.clients.themeparks_errors import ThemeParksUnavailableError


class OfflineRuntime:
    def now(self) -> datetime:
        return NOW

    def build(self, scenario: Scenario) -> Built:
        offline = scenario.offline
        assert offline is not None, f"{scenario.id} has no offline spec"
        down = set(offline.down)
        intake, sessions = make_intake()
        snapshots = InMemorySnapshotRepository()
        if "empty_store" not in down:
            seed_snapshot(
                snapshots, snapshot_context(retrieved_at=NOW - timedelta(minutes=5))
            )
        collector = (
            FakeCollector(error=ThemeParksUnavailableError("provider down"))
            if "provider" in down
            else None
        )
        deps = make_deps(sessions=sessions, snapshots=snapshots, collector=collector)
        checkpointer = default_checkpointer()
        recorder = RecordingExtractor(FakeExtractor(offline.extraction))
        graph = ipg.build_initial_planning_graph(
            recorder,
            intake,
            checkpointer,
            deps_factory=factory_for(deps),
            clock=lambda: NOW,
            names=make_names(),
            narrator=NoNarrator(),
        )

        def replanning() -> Any:
            return rg.build_replanning_graph(
                checkpointer,
                deps_factory=factory_for(deps),
                clock=lambda: NOW,
                narrator=NoNarrator(),
            )

        return Built(graph, recorder, replanning)

    def context(self, scenario: Scenario) -> AbstractContextManager[Any]:
        return nullcontext()

    def height_restricted(self) -> list[str]:
        return sorted(a.node_id for a in catalog() if a.height_restriction_cm)

    def rows(self, scenario: Scenario) -> int | None:
        return None
