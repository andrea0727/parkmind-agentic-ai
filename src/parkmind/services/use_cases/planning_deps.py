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

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from parkmind.core.contracts import PARK_TZ, AccessibilityRequirements, Attraction, Park
from parkmind.services.clients.knowledge import magic_kingdom_knowledge_store
from parkmind.services.clients.open_meteo_client import OpenMeteoClient
from parkmind.services.clients.postgres import (
    PostgresAttractionRepository,
    PostgresIdMappingRepository,
    PostgresPlanRepository,
    PostgresProposalRepository,
    PostgresSnapshotRepository,
    connect,
)
from parkmind.services.clients.routing_client import RoutingClient
from parkmind.services.clients.themeparks_client import ThemeParksClient
from parkmind.services.clients.themeparks_errors import ThemeParksClientError
from parkmind.services.ports import (
    AttractionRepository,
    IdMappingRepository,
    KnowledgeStore,
    PlanRepository,
    ProposalRepository,
    RepositoryUnavailableError,
    RoutingError,
    RoutingPort,
    SessionStore,
    SnapshotRepository,
    StoredDataError,
)
from parkmind.services.use_cases.collect_snapshot import (
    CollectResult,
    SnapshotCollector,
)
from parkmind.services.use_cases.session_memory import session_store

logger = logging.getLogger(__name__)

MAGIC_KINGDOM_PARK_ID = "75ea578a-adc8-4116-a54d-dccb60765ef9"  # ThemeParks Wiki entity id


class PlanningUnavailableError(RuntimeError):
    """Planning cannot go on: a port it needs failed or has no data. The graph ends with a plain message."""


class ContextUnavailableError(PlanningUnavailableError):
    """No usable park data: no live snapshot, no valid stored one, or no schedule/catalog."""


class SnapshotCollecting(Protocol):
    def collect(self, *, now: datetime) -> CollectResult: ...


class ParkDataSource(Protocol):
    """The park-data provider (ThemeParks): where a missing catalog or schedule is fetched."""

    def get_catalog(self) -> list[Attraction]: ...

    def get_schedule(self, on_date: date) -> Park: ...


@dataclass(frozen=True)
class PlanningDeps:
    park_id: str
    attractions: AttractionRepository
    snapshots: SnapshotRepository
    id_mappings: IdMappingRepository
    knowledge: KnowledgeStore
    routing: RoutingPort
    sessions: SessionStore
    plans: PlanRepository
    proposals: ProposalRepository
    collector: SnapshotCollecting | None = None
    """``None`` plans from the latest stored snapshot only (no provider call)."""
    transaction: Callable[[], AbstractContextManager[Any]] = field(default=nullcontext)
    """One atomic unit over ``plans`` and ``proposals`` (a DB transaction by default)."""
    park_data: ParkDataSource | None = None
    """Fetches (and caches in ``attractions``) a catalog or schedule the database lacks."""


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
            plans=PostgresPlanRepository(conn),
            proposals=PostgresProposalRepository(conn),
            collector=SnapshotCollector(parks, weather, snapshots, id_mappings),
            transaction=lambda: conn.transaction(),
            park_data=parks,
        )


@contextmanager
def open_deps(deps_factory: DepsFactory) -> Iterator[PlanningDeps]:
    """``deps_factory()``, with a failing port reported as ``PlanningUnavailableError``.

    The use cases open their ports here, so the graph handles one error type
    instead of every adapter's.
    """
    try:
        with deps_factory() as deps:
            yield deps
    except PlanningUnavailableError:
        raise
    except (
        RoutingError,
        RepositoryUnavailableError,
        StoredDataError,
        ThemeParksClientError,
    ) as exc:
        raise PlanningUnavailableError(f"{type(exc).__name__}: {exc}") from exc


def load_catalog(deps: PlanningDeps) -> list[Attraction]:
    """The stored catalog; an empty store is filled once from the provider (the DB is a cache)."""
    catalog = deps.attractions.list_attractions(deps.park_id)
    if catalog:
        return catalog
    if deps.park_data is None:
        raise ContextUnavailableError(f"no attraction catalog for park {deps.park_id!r}")
    catalog = deps.park_data.get_catalog()
    if not catalog:
        raise ContextUnavailableError(f"the provider returned no catalog for park {deps.park_id!r}")
    deps.attractions.save_catalog(deps.park_id, catalog)
    return catalog


def load_park(deps: PlanningDeps, now: datetime) -> Park:
    """Today's operating window; without it nothing can be bounded, so fail closed.

    Read from the store, else fetched from the provider and stored for the next call.
    """
    return load_schedule(deps, now.astimezone(PARK_TZ).date())


def load_schedule(deps: PlanningDeps, on_date: date) -> Park:
    """The operating window on ``on_date`` (park calendar): store first, then the provider."""
    park = deps.attractions.get_schedule(deps.park_id, on_date)
    if park is not None:
        return park
    if deps.park_data is None:
        raise ContextUnavailableError(f"no schedule for park {deps.park_id!r} on {on_date}")
    park = deps.park_data.get_schedule(on_date)
    deps.attractions.save_schedule(park)
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
        try:
            with open_deps(self._deps_factory) as deps:
                return load_catalog(deps)
        except PlanningUnavailableError:
            logger.warning("attraction names cannot be resolved: no catalog", exc_info=True)
            return []


def deferred_attraction_repository(deps_factory: DepsFactory) -> AttractionRepository:
    return _DeferredAttractionRepository(deps_factory)  # type: ignore[return-value]
