"""Use case: the park-data reads behind the ``data.*`` tools (P0-25; Architecture section 29).

Each method answers one question from **one** current snapshot, read the way
LOAD CONTEXT reads it (``current_snapshot``: fresh collection, else the latest
valid stored snapshot), and returns the snapshot's stamp with the answer, so a
caller always knows which snapshot a number came from and how old it is
(P0-25: "Responses based on live data carry the snapshot id and its age").

Provider formats never leave this layer: answers are section 33 contracts
(``WaitEstimate``, ``AttractionStatus``, ``WeatherHour``, ``Attraction``,
``Park``). Waits are standby only (section 29); showtimes are the scheduled
performances the normalizer kept -- availability windows (``Operating``) and
separately ticketed events never become showtimes (P0-10). Unknown ids are
reported back instead of failing the whole call.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Generic, TypeVar

from parkmind.core.contracts import (
    Attraction,
    AttractionStatus,
    DataSource,
    LiveContext,
    Park,
    WaitEstimate,
    WeatherHour,
)
from parkmind.services.ports import WalkEstimate, WalkEstimator
from parkmind.services.use_cases.latest_snapshot import DEFAULT_MAX_AGE
from parkmind.services.use_cases.load_context import ContextSource, current_snapshot
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    PlanningDeps,
    PlanningUnavailableError,
    default_planning_deps,
    load_catalog,
    load_schedule,
    open_deps,
)

T = TypeVar("T")

DEFAULT_WEATHER_HORIZON = timedelta(hours=12)
_HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class SnapshotStamp:
    """Which snapshot answered, and how old it was at ``now``."""

    snapshot_id: str
    retrieved_at: datetime
    age: timedelta
    stale: bool
    """Outside ``0 <= age <= max_age``, the window rule 11 trusts (a future-dated snapshot is stale too)."""
    origin: ContextSource
    data_sources: tuple[DataSource, ...]


@dataclass(frozen=True)
class FromSnapshot(Generic[T]):
    value: T
    stamp: SnapshotStamp


@dataclass(frozen=True)
class Lookup(Generic[T]):
    """Answers for the ids that have one, and the ids that don't."""

    found: T
    unknown_ids: list[str] = field(default_factory=list)
    """Not in the park's catalog."""
    no_reading_ids: list[str] = field(default_factory=list)
    """In the catalog, but the snapshot has no value for them right now."""


class ParkDataQueries:
    def __init__(
        self,
        deps_factory: DepsFactory = default_planning_deps,
        *,
        max_age: timedelta = DEFAULT_MAX_AGE,
    ) -> None:
        self._deps_factory = deps_factory
        self._max_age = max_age

    # -- snapshot-backed --------------------------------------------------------------

    def live_waits(
        self, attraction_ids: Sequence[str] | None, now: datetime
    ) -> FromSnapshot[Lookup[list[WaitEstimate]]]:
        with open_deps(self._deps_factory) as deps:
            context, stamp = self._snapshot(deps, now)
            ids, unknown = _requested(
                attraction_ids, load_catalog(deps), default=context.waits
            )
        waits = [context.waits[i] for i in ids if i in context.waits]
        no_reading = [i for i in ids if i not in context.waits]
        return FromSnapshot(Lookup(waits, unknown, no_reading), stamp)

    def attraction_status(
        self, attraction_ids: Sequence[str] | None, now: datetime
    ) -> FromSnapshot[Lookup[dict[str, AttractionStatus]]]:
        with open_deps(self._deps_factory) as deps:
            context, stamp = self._snapshot(deps, now)
            ids, unknown = _requested(
                attraction_ids, load_catalog(deps), default=context.statuses
            )
        statuses = {i: context.statuses[i] for i in ids if i in context.statuses}
        no_reading = [i for i in ids if i not in context.statuses]
        return FromSnapshot(Lookup(statuses, unknown, no_reading), stamp)

    def showtimes(
        self, show_ids: Sequence[str] | None, now: datetime
    ) -> FromSnapshot[Lookup[dict[str, list[datetime]]]]:
        with open_deps(self._deps_factory) as deps:
            context, stamp = self._snapshot(deps, now)
            ids, unknown = _requested(
                show_ids, load_catalog(deps), default=context.showtimes
            )
        times = {i: sorted(context.showtimes[i]) for i in ids if i in context.showtimes}
        no_reading = [i for i in ids if i not in context.showtimes]
        return FromSnapshot(Lookup(times, unknown, no_reading), stamp)

    def weather(
        self, now: datetime, start: datetime | None = None, end: datetime | None = None
    ) -> FromSnapshot[list[WeatherHour]]:
        """The hours overlapping ``[start, end]``; by default from ``now`` over the next 12 hours.

        A ``WeatherHour`` is stamped with the start of its hour, so the hour
        that contains ``start`` is included.
        """
        start = start or now
        end = end or start + DEFAULT_WEATHER_HORIZON
        if end < start:
            raise ValueError("end must not be before start")
        with open_deps(self._deps_factory) as deps:
            context, stamp = self._snapshot(deps, now)
        hours = [
            h
            for h in context.weather
            if h.timestamp + _HOUR > start and h.timestamp <= end
        ]
        return FromSnapshot(sorted(hours, key=lambda h: h.timestamp), stamp)

    # -- not snapshot-backed ----------------------------------------------------------

    def schedule(self, on_date: date) -> Park:
        with open_deps(self._deps_factory) as deps:
            return load_schedule(deps, on_date)

    def attraction_info(
        self, attraction_ids: Sequence[str] | None
    ) -> Lookup[list[Attraction]]:
        with open_deps(self._deps_factory) as deps:
            catalog = load_catalog(deps)
        by_id = {a.node_id: a for a in catalog}
        if attraction_ids is None:
            return Lookup(sorted(catalog, key=lambda a: a.node_id))
        ids = list(dict.fromkeys(attraction_ids))
        return Lookup(
            [by_id[i] for i in ids if i in by_id],
            unknown_ids=[i for i in ids if i not in by_id],
        )

    def walking_time(
        self, origin_node_id: str, destination_node_id: str
    ) -> WalkEstimate:
        with open_deps(self._deps_factory) as deps:
            routing = deps.routing
            if not isinstance(routing, WalkEstimator):
                raise PlanningUnavailableError(
                    f"the routing adapter {type(routing).__name__} cannot say how it estimates"
                )
            return routing.estimate_walk(origin_node_id, destination_node_id)

    # -- helpers ----------------------------------------------------------------------

    def _snapshot(
        self, deps: PlanningDeps, now: datetime
    ) -> tuple[LiveContext, SnapshotStamp]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        context, origin = current_snapshot(deps, now, max_age=self._max_age)
        age = now - context.retrieved_at
        sources = deps.snapshots.get_data_sources(context.snapshot_id) or []
        stamp = SnapshotStamp(
            snapshot_id=context.snapshot_id,
            retrieved_at=context.retrieved_at,
            age=age,
            stale=not (timedelta(0) <= age <= self._max_age),
            origin=origin,
            data_sources=tuple(sources),
        )
        return context, stamp


def _requested(
    ids: Sequence[str] | None,
    catalog: Sequence[Attraction],
    *,
    default: Mapping[str, object],
) -> tuple[list[str], list[str]]:
    """The ids to answer for, and the requested ids the catalog doesn't know.

    ``None`` means every catalog id the snapshot has a value for.
    """
    known = {a.node_id for a in catalog}
    if ids is None:
        return sorted(i for i in default if i in known), []
    requested = list(dict.fromkeys(ids))
    return [i for i in requested if i in known], [
        i for i in requested if i not in known
    ]
