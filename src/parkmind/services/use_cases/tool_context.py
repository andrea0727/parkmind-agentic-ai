"""LOAD CONTEXT through the ``data.*`` / ``knowledge.*`` capabilities (P0-24; Architecture section 27 [C23]).

``assemble_from_port`` builds the party's ``LiveContext`` from a
``ContextDataPort``: catalog, schedule, waits, statuses, showtimes and weather,
then one accessibility check per guest over the catalog. Two adapters answer
it -- ``InProcessContextData`` here (the same use cases the MCP handlers call)
and the MCP client (``clients/mcp/context_client.py``) -- and
``PARKMIND_CONTEXT_TRANSPORT`` picks which one LOAD CONTEXT uses
(``configured_context_data``). ``in_process`` (the default) keeps LOAD CONTEXT
on its direct snapshot path; ``mcp`` routes it here, with the in-process
adapter as the fallback through the same port.

The assembled context is the snapshot's, restricted to the catalog (all that
planning reads): every snapshot-backed answer must name the same snapshot, or
the assembly is refused (``ContextTransportError``). A transport's failures
propagate unchanged -- LOAD CONTEXT answers any of them in-process -- except a
missing schedule, which is a coverage gap on every path. Coverage is judged by the
normalizer's own rule (``coverage_report``), not a copy of it. Accessibility is
asked with each guest's *derived* flags; the requirements stay in this process
[C19], and the tool trace -- which is checkpointed with the context -- records
tool names and counts only, never flags or guest ids.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TypeVar

from parkmind.config.settings import settings
from parkmind.core.contracts import (
    PARK_TZ,
    AccessibilityCheck,
    AccessibilityRequirements,
    Attraction,
    AttractionStatus,
    LiveContext,
    Park,
    RideRestriction,
    ToolCall,
    WaitEstimate,
    WeatherHour,
)
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
    MAGIC_KINGDOM_SCHEDULED_SHOWS,
)
from parkmind.services.ports import (
    ContextDataPort,
    ContextDataUnavailableError,
    ContextTransportError,
    FromSnapshotRef,
    SnapshotRef,
)
from parkmind.services.use_cases.check_accessibility import guest_flags
from parkmind.services.use_cases.current_snapshot import ContextSource
from parkmind.services.use_cases.knowledge_queries import KnowledgeQueries
from parkmind.services.use_cases.park_data_queries import FromSnapshot, ParkDataQueries
from parkmind.services.use_cases.planning_deps import (
    ContextUnavailableError,
    DepsFactory,
)
from parkmind.services.use_cases.snapshot_normalization import coverage_report

_ACCESSIBILITY_BATCH = 100  # knowledge.check_accessibility takes at most 100 ids

T = TypeVar("T")
V = TypeVar("V")


class InProcessContextData:
    """``ContextDataPort`` over the use cases, in this process."""

    transport = "in_process"

    def __init__(
        self, deps_factory: DepsFactory, clock: Callable[[], datetime]
    ) -> None:
        self._data = ParkDataQueries(deps_factory)
        self._deps_factory = deps_factory
        self._clock = clock
        self._knowledge: KnowledgeQueries | None = None

    def session(self) -> AbstractContextManager[None]:
        return nullcontext()

    def catalog(self) -> list[Attraction]:
        return self._data.attraction_info(None).found

    def schedule(self, on_date: date) -> Park:
        return self._data.schedule(on_date)

    def live_waits(self) -> FromSnapshotRef[list[WaitEstimate]]:
        return _ref(self._data.live_waits(None, self._clock()), lambda v: v.found)

    def attraction_statuses(self) -> FromSnapshotRef[dict[str, AttractionStatus]]:
        return _ref(
            self._data.attraction_status(None, self._clock()), lambda v: v.found
        )

    def showtimes(self) -> FromSnapshotRef[dict[str, list[datetime]]]:
        return _ref(self._data.showtimes(None, self._clock()), lambda v: v.found)

    def weather(
        self, start: datetime, end: datetime
    ) -> FromSnapshotRef[list[WeatherHour]]:
        return _ref(self._data.weather(self._clock(), start, end), lambda v: v)

    def check_accessibility(
        self,
        guest_id: str,
        flags: frozenset[RideRestriction],
        attraction_ids: Sequence[str],
    ) -> list[AccessibilityCheck]:
        if self._knowledge is None:
            self._knowledge = KnowledgeQueries(self._deps_factory)
        return self._knowledge.check_accessibility(
            attraction_ids, sorted(flags), guest_id
        ).checks


def _ref(answer: FromSnapshot[V], value: Callable[[V], T]) -> FromSnapshotRef[T]:
    stamp = answer.stamp
    return FromSnapshotRef(
        value(answer.value),
        SnapshotRef(stamp.snapshot_id, stamp.retrieved_at, stamp.origin),
    )


def configured_context_data() -> ContextDataPort | None:
    """The port ``PARKMIND_CONTEXT_TRANSPORT`` selects; ``None`` keeps the direct path.

    The MCP client is imported only when selected: in-process LOAD CONTEXT never
    loads the MCP SDK.
    """
    if settings.CONTEXT_TRANSPORT != "mcp":
        return None
    from parkmind.services.clients.mcp.context_client import McpContextData

    return McpContextData(settings.MCP_URL)


@dataclass(frozen=True)
class AssembledContext:
    base: LiveContext
    """The snapshot's context over the catalog, with coverage and the tool trace."""
    checks: list[AccessibilityCheck]
    origin: ContextSource


def assemble_from_port(
    port: ContextDataPort,
    now: datetime,
    requirements: Sequence[AccessibilityRequirements],
) -> AssembledContext:
    """The party's base context and accessibility checks, from ``port``'s answers."""
    with port.session():
        return _assemble(port, now, requirements)


def _assemble(
    port: ContextDataPort,
    now: datetime,
    requirements: Sequence[AccessibilityRequirements],
) -> AssembledContext:
    trace: list[ToolCall] = []

    def traced(tool: str, count: int, **query: object) -> None:
        trace.append(
            ToolCall(
                tool_name=f"{port.transport}:{tool}",
                query=dict(query),
                iteration=1,
                result_count=count,
            )
        )

    catalog = port.catalog()
    traced("data.get_attraction_info", len(catalog))
    service_date = now.astimezone(PARK_TZ).date()
    gaps: list[str] = []
    park: Park | None
    try:
        park = port.schedule(service_date)
        traced("data.get_schedule", 1, service_date=service_date.isoformat())
    except (ContextUnavailableError, ContextDataUnavailableError):
        park = None
        gaps.append("no park schedule: weather coverage can't be checked")
        traced("data.get_schedule", 0, service_date=service_date.isoformat())

    waits = port.live_waits()
    statuses = port.attraction_statuses()
    showtimes = port.showtimes()
    start, end = _day_window(park, service_date)
    weather = port.weather(start, end)
    traced("data.get_live_waits", len(waits.value))
    traced("data.get_attraction_status", len(statuses.value))
    traced("data.get_showtimes", len(showtimes.value))
    traced(
        "data.get_weather",
        len(weather.value),
        start=start.isoformat(),
        end=end.isoformat(),
    )

    snapshots = {r.snapshot.snapshot_id for r in (waits, statuses, showtimes, weather)}
    if len(snapshots) != 1:
        raise ContextTransportError(
            f"the tools answered from different snapshots: {sorted(snapshots)}"
        )
    ref = waits.snapshot
    coverage = coverage_report(
        curated=MAGIC_KINGDOM_ATTRACTION_METADATA,
        scheduled_shows=MAGIC_KINGDOM_SCHEDULED_SHOWS,
        statuses=statuses.value,
        showtimes=showtimes.value,
        weather=weather.value,
        park=park,
        gaps=gaps,
    )
    base = LiveContext(
        snapshot_id=ref.snapshot_id,
        retrieved_at=ref.retrieved_at,
        waits={w.attraction_id: w for w in waits.value},
        statuses=statuses.value,
        showtimes=showtimes.value,
        weather=weather.value,
        coverage=coverage,
        tool_trace=trace,
    )

    ids = [a.node_id for a in catalog]
    checks: list[AccessibilityCheck] = []
    for requirement in requirements:
        flags = guest_flags(requirement)
        for start_at in range(0, len(ids), _ACCESSIBILITY_BATCH):
            batch = ids[start_at : start_at + _ACCESSIBILITY_BATCH]
            checks += port.check_accessibility(requirement.guest_id, flags, batch)
    if requirements:
        traced("knowledge.check_accessibility", len(checks), guests=len(requirements))
    base = base.model_copy(update={"tool_trace": trace})
    origin: ContextSource = "live" if ref.origin == "live" else "snapshot"
    return AssembledContext(base, checks, origin)


def _day_window(park: Park | None, service_date: date) -> tuple[datetime, datetime]:
    """Opening to closing; the whole calendar day when the schedule is unknown."""
    if park is not None:
        return park.opening_time, park.closing_time
    start = datetime.combine(service_date, time(0), tzinfo=PARK_TZ)
    return start, start + timedelta(hours=23, minutes=59)
