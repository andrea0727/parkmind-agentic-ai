"""The live runtime: real ports, the real Anthropic extractor and narrator, injected infrastructure faults."""

from contextlib import AbstractContextManager, ExitStack
from datetime import datetime
from typing import Any

from e2e.kit.driver import Built
from e2e.kit.env import EDGE_URL, e2e_now, load_live_env
from e2e.kit.facts import attraction_rows, height_restricted_nodes
from e2e.kit.faults import inject, park_open
from e2e.kit.recorder import RecordingExtractor
from e2e.model import Scenario

CATALOG_FILLING = "happy_catalogo_vacio"


class LiveRuntime:
    def now(self) -> datetime:
        return e2e_now()

    def build(self, scenario: Scenario) -> Built:
        load_live_env()
        from parkmind.agents.elicit_agent import AnthropicExtractor
        from parkmind.graph import initial_planning_graph as ipg
        from parkmind.graph import replanning_graph as rg
        from parkmind.graph.checkpointing import default_checkpointer

        recorder = RecordingExtractor(AnthropicExtractor())
        checkpointer = default_checkpointer()
        graph = ipg.build_initial_planning_graph(
            recorder, None, checkpointer, clock=e2e_now
        )

        def replanning() -> Any:
            return rg.build_replanning_graph(checkpointer, clock=e2e_now)

        return Built(graph, recorder, replanning)

    def context(self, scenario: Scenario) -> AbstractContextManager[Any]:
        stack = ExitStack()
        stack.enter_context(park_open())
        stack.enter_context(inject(scenario.fault))
        return stack

    def rows(self, scenario: Scenario) -> int | None:
        return attraction_rows(EDGE_URL) if scenario.id == CATALOG_FILLING else None

    def height_restricted(self) -> list[str]:
        return height_restricted_nodes(EDGE_URL)
