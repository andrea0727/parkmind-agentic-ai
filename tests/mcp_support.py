"""Realistic ports for the MCP tool tests (P0-24..P0-27), all offline.

The real 2026-09-27 park capture (``capture.Provider``) collected through the real
``SnapshotCollector``, the real Magic Kingdom catalog fixture, the reviewed
notice corpus and the strict ``RoutingClient``; only storage is in memory. No
network, no Postgres, no MCP server running beforehand.
"""

import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from capture import NOW, PARK_ID, Provider
from elicit_support import FakeSessionStore
from fakes import (
    InMemoryIdMappingRepository,
    InMemoryPlanRepository,
    InMemoryProposalRepository,
    InMemorySnapshotRepository,
)
from mcp import Client
from planning_support import factory_for

from parkmind.core.contracts import Attraction, Park
from parkmind.services.clients.knowledge import magic_kingdom_knowledge_store
from parkmind.services.clients.routing_client import RoutingClient
from parkmind.services.clients.themeparks_normalize import parse_catalog
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
    MAGIC_KINGDOM_EXCLUDED_ENTITIES,
)
from parkmind.services.use_cases.collect_snapshot import (
    CollectResult,
    SnapshotCollector,
)
from parkmind.services.use_cases.planning_deps import PlanningDeps
from parkmind.tools.mcp_server import create_server
from parkmind.tools.registry import ToolContext, ToolRegistry, build_registry

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CHILDREN = FIXTURES / "themeparks" / "children_magic_kingdom_2026-10-01.json"

__all__ = ["NOW", "PARK_ID", "CatalogRepository", "Deps", "call_tool", "list_tools"]


def mk_catalog() -> list[Attraction]:
    payload = json.loads(CHILDREN.read_text(encoding="utf-8"))
    return parse_catalog(
        payload,
        MAGIC_KINGDOM_ATTRACTION_METADATA,
        excluded=frozenset(MAGIC_KINGDOM_EXCLUDED_ENTITIES),
    ).attractions


def capture_park() -> Park:
    with Provider().parks() as client:
        return client.get_schedule(NOW.date())


class CatalogRepository:
    """AttractionRepository over fixed catalog and schedules, in memory."""

    def __init__(
        self, catalog: Sequence[Attraction], schedules: dict[date, Park]
    ) -> None:
        self._catalog = list(catalog)
        self._schedules = dict(schedules)

    def list_attractions(self, park_id: str) -> list[Attraction]:
        return list(self._catalog) if park_id == PARK_ID else []

    def get_schedule(self, park_id: str, on_date: date) -> Park | None:
        return self._schedules.get(on_date) if park_id == PARK_ID else None

    def save_catalog(self, park_id: str, attractions: Sequence[Attraction]) -> None:
        self._catalog = list(attractions)

    def save_schedule(self, park: Park) -> None:
        self._schedules[park.opening_time.date()] = park


class Deps:
    """A ``PlanningDeps`` on the capture, plus handles the tests inspect."""

    def __init__(
        self,
        *,
        collect_at: datetime | None = NOW,
        live_collector: bool = False,
        routing: Any = None,
        schedules: dict[date, Park] | None = None,
        sessions: FakeSessionStore | None = None,
        knowledge_search: Any = None,
    ) -> None:
        self.snapshots = InMemorySnapshotRepository()
        self.id_mappings = InMemoryIdMappingRepository()
        self.proposals = InMemoryProposalRepository()
        self.plans = InMemoryPlanRepository(self.proposals)
        self.sessions = sessions if sessions is not None else FakeSessionStore()
        self.collected: CollectResult | None = None
        if collect_at is not None:
            self.collected = self._collector().collect(now=collect_at)
        park = capture_park()
        self.value = PlanningDeps(
            park_id=PARK_ID,
            attractions=CatalogRepository(
                mk_catalog(),
                schedules
                if schedules is not None
                else {park.opening_time.date(): park},
            ),  # type: ignore[arg-type]
            snapshots=self.snapshots,  # type: ignore[arg-type]
            id_mappings=self.id_mappings,  # type: ignore[arg-type]
            knowledge=magic_kingdom_knowledge_store(),
            routing=routing if routing is not None else RoutingClient(),
            sessions=self.sessions,  # type: ignore[arg-type]
            plans=self.plans,  # type: ignore[arg-type]
            proposals=self.proposals,  # type: ignore[arg-type]
            collector=self._collector() if live_collector else None,
            knowledge_search=knowledge_search,
        )

    def _collector(self) -> SnapshotCollector:
        provider = Provider()
        return SnapshotCollector(
            provider.parks(), provider.weather(), self.snapshots, self.id_mappings
        )

    def registry(self, clock: Callable[[], datetime] = lambda: NOW) -> ToolRegistry:
        return build_registry(ToolContext(factory_for(self.value), clock))


def list_tools(registry: ToolRegistry) -> Any:
    async def run() -> Any:
        async with Client(create_server(registry)) as client:
            return (await client.list_tools()).tools

    return asyncio.run(run())


def call_tool(
    registry: ToolRegistry, name: str, arguments: dict[str, Any] | None = None
) -> Any:
    """One MCP round trip, in process: returns the ``CallToolResult``."""

    async def run() -> Any:
        async with Client(create_server(registry)) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(run())
