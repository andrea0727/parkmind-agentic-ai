"""The ports the initial planning use cases run on (P0-30).

Use cases take a ``DepsFactory`` -- a context manager yielding ``PlanningDeps`` --
so each call owns its connection (repositories are built on a caller-owned one)
and tests can swap in fakes. ``default_planning_deps`` binds the Postgres
repositories, the routing client, the notice corpus and the snapshot collector.
Nothing here imports MCP: the data/knowledge ports can be pointed at an MCP
client later by supplying another factory.

``AccessibilityRequirements`` are read through ``load_requirements`` from the
SessionStore, per call, and handed straight to the deterministic core. They are
never returned into graph state [C19].
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from parkmind.core.contracts import PARK_TZ, AccessibilityRequirements, Attraction, Park
from parkmind.services.clients.knowledge import magic_kingdom_knowledge_store
from parkmind.services.clients.open_meteo_client import OpenMeteoClient
from parkmind.services.clients.postgres import (
    PostgresAttractionRepository,
    PostgresIdMappingRepository,
    PostgresSnapshotRepository,
    connect,
)
from parkmind.services.clients.routing_client import RoutingClient
from parkmind.services.clients.themeparks_client import ThemeParksClient
from parkmind.services.ports import (
    AttractionRepository,
    IdMappingRepository,
    KnowledgeStore,
    RoutingPort,
    SessionStore,
    SnapshotRepository,
)
from parkmind.services.use_cases.collect_snapshot import (
    CollectResult,
    SnapshotCollector,
)
from parkmind.services.use_cases.session_memory import session_store

MAGIC_KINGDOM_PARK_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"  # ThemeParks Wiki entity id


class ContextUnavailableError(RuntimeError):
    """No usable park data: no live snapshot, no valid stored one, or no schedule/catalog."""


class SnapshotCollecting(Protocol):
    def collect(self, *, now: datetime) -> CollectResult: ...


@dataclass(frozen=True)
class PlanningDeps:
    park_id: str
    attractions: AttractionRepository
    snapshots: SnapshotRepository
    id_mappings: IdMappingRepository
    knowledge: KnowledgeStore
    routing: RoutingPort
    sessions: SessionStore
    collector: SnapshotCollecting | None = None
    """``None`` plans from the latest stored snapshot only (no provider call)."""


DepsFactory = Callable[[], AbstractContextManager[PlanningDeps]]


@contextmanager
def default_planning_deps() -> Iterator[PlanningDeps]:
    with ExitStack() as stack:
        conn = stack.enter_context(connect())
        parks = stack.enter_context(ThemeParksClient(MAGIC_KINGDOM_PARK_ID))
        weather = stack.enter_context(OpenMeteoClient())
        snapshots = PostgresSnapshotRepository(conn)
        id_mappings = PostgresIdMappingRepository(conn)
        yield PlanningDeps(
            park_id=MAGIC_KINGDOM_PARK_ID,
            attractions=PostgresAttractionRepository(conn),
            snapshots=snapshots,
            id_mappings=id_mappings,
            knowledge=magic_kingdom_knowledge_store(),
            routing=RoutingClient(),
            sessions=session_store(conn),
            collector=SnapshotCollector(parks, weather, snapshots, id_mappings),
        )


def load_catalog(deps: PlanningDeps) -> list[Attraction]:
    catalog = deps.attractions.list_attractions(deps.park_id)
    if not catalog:
        raise ContextUnavailableError(f"no attraction catalog for park {deps.park_id!r}")
    return catalog


def load_park(deps: PlanningDeps, now: datetime) -> Park:
    """Today's operating window; without it nothing can be bounded, so fail closed."""
    park = deps.attractions.get_schedule(deps.park_id, now.astimezone(PARK_TZ).date())
    if park is None:
        raise ContextUnavailableError(f"no schedule for park {deps.park_id!r} on this date")
    return park


def load_requirements(
    deps: PlanningDeps, thread_id: str, guest_ids: Sequence[str]
) -> tuple[list[AccessibilityRequirements], list[str]]:
    """The stored requirements of ``guest_ids`` and the ids with no record on file."""
    found: list[AccessibilityRequirements] = []
    missing: list[str] = []
    for guest_id in guest_ids:
        record = deps.sessions.get(thread_id, guest_id)
        if record is None:
            missing.append(guest_id)
        else:
            found.append(record)
    return found, missing


class _DeferredAttractionRepository:
    """The deps' catalog through a fresh ``PlanningDeps`` per call (for the name resolver)."""

    def __init__(self, deps_factory: DepsFactory) -> None:
        self._deps_factory = deps_factory

    def list_attractions(self, park_id: str) -> list[Attraction]:
        with self._deps_factory() as deps:
            return deps.attractions.list_attractions(deps.park_id)


def deferred_attraction_repository(deps_factory: DepsFactory) -> AttractionRepository:
    return _DeferredAttractionRepository(deps_factory)  # type: ignore[return-value]
