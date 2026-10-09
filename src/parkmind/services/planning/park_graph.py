"""ParkGraph — the planning space: attractions, lands, opening hours, showtimes.

Planning components use ParkGraph to query park topology (walking times,
operating windows, show starts, land membership, eligibility filters)
without knowing which concrete routing adapter, data source or alias
registry is providing the data. Everything the graph knows is injected
at construction time, so the planning core stays free of adapter and
client imports (see import-linter contract ``core-forbidden-imports``).

The ``from_sources`` classmethod is the standard composer: feed it the
``Attraction`` catalog, a ``Park`` schedule, a ``LiveContext`` snapshot
(for showtimes + statuses) and a land-alias registry curated at the
``services/clients`` layer, and it builds the indexes the planning
methods need.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from parkmind.core.contracts import (
    Attraction,
    AttractionCategory,
    AttractionStatus,
    LiveContext,
    Park,
)
from parkmind.services.ports import RoutingPort

_LOCATIVE_PREFIXES: tuple[str, ...] = (
    "close to ",
    "right by ",
    "next to ",
    "near ",
    "around ",
    "inside ",
    "within ",
    "in ",
    "at ",
    "by ",
)


def _normalize_query(query: str) -> str:
    """Lowercase, collapse whitespace, strip a leading locative prefix."""
    cleaned = " ".join(query.lower().split())
    for prefix in _LOCATIVE_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    return cleaned


@dataclass(frozen=True)
class ParkGraph:
    """Planning topology for the theme park, deterministic over its inputs.

    A frozen value type: once built via ``from_sources`` (the standard
    composer) its indexes are immutable and every query is a pure function
    of the injected catalog + schedule + live snapshot + alias registry.

    Fields
    ------
    - ``routing``:            port used by ``walk_minutes`` (never queried directly).
    - ``park``:               operating window used by ``open_at``.
    - ``attractions``:        ``node_id -> Attraction`` for every catalog node.
    - ``land_of``:            ``node_id -> canonical land name`` (``"Unknown"`` for orphans).
    - ``nodes_by_land``:      ``land -> (node_id, ...)`` sorted by attraction name.
    - ``land_aliases``:       normalized alias -> canonical land, used by ``resolve_location``.
    - ``showtimes_by_node``:  ``show_id -> (datetime, ...)`` from ``LiveContext.showtimes``.
    - ``statuses``:           ``node_id -> AttractionStatus`` from ``LiveContext.statuses``.
    """

    routing: RoutingPort
    park: Park
    attractions: Mapping[str, Attraction]
    land_of: Mapping[str, str]
    nodes_by_land: Mapping[str, tuple[str, ...]]
    land_aliases: Mapping[str, str] = field(default_factory=dict)
    showtimes_by_node: Mapping[str, tuple[datetime, ...]] = field(default_factory=dict)
    statuses: Mapping[str, AttractionStatus] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Fail fast when ``routing`` is missing: the graph cannot answer any
        topology question without a ``RoutingPort``, so every call site would
        otherwise crash later with a less actionable error.
        """
        if self.routing is None:
            raise TypeError("ParkGraph requires a valid RoutingPort instance")

    def walk_minutes(self, a: str, b: str) -> float:
        """Estimate walking time in minutes between two park planning nodes."""
        return self.routing.walk_minutes(a, b)

    def open_at(self, node_id: str, t: datetime) -> bool:
        """True iff ``node_id`` is a known node, is operating and ``t`` falls
        inside the park's opening window on the schedule date.

        The window is half-open ``[opening_time, closing_time)``: a stop that
        would start exactly at ``closing_time`` is not plannable, which keeps
        the optimizer from scheduling an experience it cannot execute.

        Unknown nodes return False (explicit miss); any non-OPERATING status
        (``DOWN``, ``CLOSED``, ``REFURBISHMENT``) also returns False. A node
        with no status in ``statuses`` fails closed too — this mirrors
        ``ConstraintChecker``'s rule 1 ("status unknown ... failing closed")
        and ``Optimizer``, which only admits nodes with an explicit
        ``OPERATING`` status; defaulting to open here would let a planner
        propose stops the checker then rejects.
        """
        if node_id not in self.attractions:
            return False
        if self.statuses.get(node_id) is not AttractionStatus.OPERATING:
            return False
        return self.park.opening_time <= t < self.park.closing_time

    def showtimes(self, show_id: str) -> list[datetime]:
        """Return a fresh list of showtimes for ``show_id``, empty if unknown.

        Showtimes come from ``LiveContext`` (source of truth per P0-11); the
        curated catalog does not carry permanent show programming yet.
        """
        return list(self.showtimes_by_node.get(show_id, ()))

    def resolve_location(self, query: str) -> str | None:
        """Resolve a human alias (e.g. ``"near Frontierland"``) to a node id.

        Returns the alphabetically-first attraction node of the resolved
        land (by ``Attraction.name``), not a dedicated land node. This is a
        deviation from baseline §16/§25, which model each land as its own
        graph node (e.g. ``"frontierland"``); P0-13's Done-when only
        requires "returns a node id", so the representative-attraction
        approach satisfies it, but ``current_location_node_id`` consumers
        (e.g. the ``PARTY_RELOCATED`` event in P0-22) will measure walks
        from that attraction, not from a land-central point. Returns
        ``None`` when the alias is unknown or when the resolved land has no
        catalog nodes (explicit miss).
        """
        normalized = _normalize_query(query)
        if not normalized:
            return None
        land = self.land_aliases.get(normalized)
        if land is None:
            return None
        nodes = self.nodes_by_land.get(land, ())
        return nodes[0] if nodes else None

    def filter_candidates(
        self,
        *,
        guest_height_cm: int | None = None,
        exclude_categories: Collection[AttractionCategory] = (),
        outdoor_ok: bool = True,
        land: str | None = None,
    ) -> list[str]:
        """Return catalog node ids matching every provided eligibility filter.

        - ``guest_height_cm``: the guest's height. Attractions whose
          ``height_restriction_cm`` (the minimum height required to ride)
          exceeds this value are excluded. Attractions with no height
          restriction always pass. ``None`` disables the filter.
        - ``exclude_categories``: categories to drop (e.g. ``{THRILL}``).
        - ``outdoor_ok``: when False, outdoor attractions are excluded.
        - ``land``: restrict to this canonical land (must match ``land_of``).

        Results are sorted by ``node_id`` for determinism.
        """
        excluded = frozenset(exclude_categories)
        matches: list[str] = []
        for node_id, attraction in self.attractions.items():
            if land is not None and self.land_of.get(node_id) != land:
                continue
            if attraction.category in excluded:
                continue
            if not outdoor_ok and attraction.outdoor:
                continue
            if (
                guest_height_cm is not None
                and attraction.height_restriction_cm is not None
                and attraction.height_restriction_cm > guest_height_cm
            ):
                continue
            matches.append(node_id)
        matches.sort()
        return matches

    @classmethod
    def from_sources(
        cls,
        *,
        routing: RoutingPort,
        park: Park,
        attractions: Sequence[Attraction],
        live_context: LiveContext | None = None,
        land_aliases: Mapping[str, str] | None = None,
    ) -> "ParkGraph":
        """Compose a ``ParkGraph`` from the normalized catalog + live snapshot.

        Attractions missing a ``land`` value are grouped under ``"Unknown"``
        in ``land_of`` so they still appear in catalog lookups but will not
        match any curated alias.
        """
        by_id: dict[str, Attraction] = {}
        land_of: dict[str, str] = {}
        buckets: dict[str, list[Attraction]] = {}
        for attraction in attractions:
            by_id[attraction.node_id] = attraction
            land = attraction.land or "Unknown"
            land_of[attraction.node_id] = land
            buckets.setdefault(land, []).append(attraction)

        nodes_by_land: dict[str, tuple[str, ...]] = {
            land: tuple(a.node_id for a in sorted(bucket, key=lambda a: a.name))
            for land, bucket in buckets.items()
        }

        showtimes_by_node: Mapping[str, tuple[datetime, ...]]
        statuses: Mapping[str, AttractionStatus]
        if live_context is None:
            showtimes_by_node = {}
            statuses = {}
        else:
            showtimes_by_node = {
                node_id: tuple(times)
                for node_id, times in live_context.showtimes.items()
            }
            statuses = dict(live_context.statuses)

        return cls(
            routing=routing,
            park=park,
            attractions=MappingProxyType(by_id),
            land_of=MappingProxyType(land_of),
            nodes_by_land=MappingProxyType(nodes_by_land),
            land_aliases=MappingProxyType(dict(land_aliases) if land_aliases else {}),
            showtimes_by_node=MappingProxyType(showtimes_by_node),
            statuses=MappingProxyType(dict(statuses)),
        )
