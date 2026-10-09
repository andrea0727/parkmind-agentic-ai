"""Fakes for the P0-30 planning use cases and graph: every port in-memory, no DB, no network."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from elicit_support import PARK_ID, FakeSessionStore, catalog
from fakes import (
    InMemoryIdMappingRepository,
    InMemoryPlanRepository,
    InMemoryProposalRepository,
    InMemorySnapshotRepository,
)

from parkmind.core.contracts import (
    PARK_TZ,
    Attraction,
    AttractionStatus,
    CoverageReport,
    LiveContext,
    Park,
    WaitEstimate,
)
from parkmind.services.clients.knowledge.in_memory import InMemoryKnowledgeStore
from parkmind.services.use_cases.collect_snapshot import CollectResult
from parkmind.services.use_cases.planning_deps import DepsFactory, PlanningDeps
from parkmind.services.use_cases.snapshot_normalization import ACCESSIBILITY_GAP

DAY = datetime.now(PARK_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
NOW = DAY.replace(hour=9, minute=5)
PARK = Park(
    park_id=PARK_ID,
    name="Magic Kingdom",
    opening_time=DAY.replace(hour=9),
    closing_time=DAY.replace(hour=22),
)


class FlatRouting:
    def walk_minutes(self, a: str, b: str) -> float:
        return 0.0 if a == b else 5.0


class PlanningAttractionRepository:
    def __init__(self, attractions: Sequence[Attraction] | None = None, park: Park | None = PARK) -> None:
        self._attractions = list(catalog() if attractions is None else attractions)
        self._park = park

    def list_attractions(self, park_id: str) -> list[Attraction]:
        return sorted(self._attractions, key=lambda a: a.node_id) if park_id == PARK_ID else []

    def get_schedule(self, park_id: str, on_date: date) -> Park | None:
        return self._park if park_id == PARK_ID else None


def snapshot_context(
    attractions: Sequence[Attraction] | None = None,
    *,
    snapshot_id: str = "snap",
    retrieved_at: datetime | None = None,
    accessibility_checks_complete: bool = False,
) -> LiveContext:
    """A collector-shaped snapshot: park-wide coverage, accessibility left for load_context."""
    attractions = catalog() if attractions is None else attractions
    return LiveContext(
        snapshot_id=snapshot_id,
        retrieved_at=retrieved_at or DAY.replace(hour=9),
        waits={
            a.node_id: WaitEstimate(
                attraction_id=a.node_id, wait_minutes=15.0, status=AttractionStatus.OPERATING
            )
            for a in attractions
        },
        statuses={a.node_id: AttractionStatus.OPERATING for a in attractions},
        showtimes={},
        coverage=CoverageReport(
            required_attractions_covered=True,
            required_shows_covered=True,
            weather_covered=True,
            accessibility_checks_complete=accessibility_checks_complete,
            coverage_gaps=[]
            if accessibility_checks_complete
            else [ACCESSIBILITY_GAP],
        ),
    )


class FakeCollector:
    """Returns ``result``, or raises ``error``; ``calls`` counts invocations."""

    def __init__(self, result: CollectResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls = 0

    def collect(self, *, now: datetime) -> CollectResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def seed_snapshot(snapshots: InMemorySnapshotRepository, live: LiveContext) -> None:
    snapshots.save(live, {"seed": live.snapshot_id}, [], normalizer_version=1)


def make_deps(
    *,
    sessions: FakeSessionStore | None = None,
    snapshots: InMemorySnapshotRepository | None = None,
    collector: Any = None,
    attractions: PlanningAttractionRepository | None = None,
    knowledge: InMemoryKnowledgeStore | None = None,
    proposals: InMemoryProposalRepository | None = None,
) -> PlanningDeps:
    proposals = proposals if proposals is not None else InMemoryProposalRepository()
    return PlanningDeps(
        park_id=PARK_ID,
        attractions=attractions or PlanningAttractionRepository(),  # type: ignore[arg-type]
        snapshots=snapshots if snapshots is not None else InMemorySnapshotRepository(),  # type: ignore[arg-type]
        id_mappings=InMemoryIdMappingRepository(),  # type: ignore[arg-type]
        knowledge=knowledge or InMemoryKnowledgeStore({}, corpus_version="t"),
        routing=FlatRouting(),
        sessions=sessions if sessions is not None else FakeSessionStore(),  # type: ignore[arg-type]
        plans=InMemoryPlanRepository(proposals),
        proposals=proposals,
        collector=collector,
    )


def factory_for(deps: PlanningDeps) -> DepsFactory:
    @contextmanager
    def factory() -> Iterator[PlanningDeps]:
        yield deps

    return factory
