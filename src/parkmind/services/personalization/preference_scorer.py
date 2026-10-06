"""PreferenceScorer -- turns the resolved group objective and each guest's
profile into a utility per attraction (section 17, backlog P0-17).

Where it sits (section 35): LOAD CONTEXT -> GroupPreferenceResolver (P0-16)
-> **PreferenceScorer** -> Optimizer (P0-19) -> ConstraintChecker. The
optimizer ranks candidates by ``utility / minutes`` and never schedules a node
missing from the utilities it is given; the scorer decides how much each stop
is worth to this party, never when it happens.

Per-guest utility, only over the guest's eligible set
(``GroupObjective.per_guest_eligible``, rules 2 and 10), follows section 17's
``preference - queue - walking - risk - change``:

- preference: a base of 1, plus or minus the guest's preferred/avoided
  categories, plus ``thematic_affinity`` matched to the attraction's land
  (canonical name or a ``ParkGraph`` alias, case- and space-insensitive) or
  category. The affinity an attraction gets stays within -1..1 however many
  keys reach it. Keys that match no land or category of an attraction in the
  catalog are reported in the result, never dropped silently.
- sensitivity penalties, from attraction attributes we actually have:
  INTENSITY is HIGH when the park's published notice (P0-26a, through the
  ``KnowledgeStore`` port) flags high g-force or motion sensitivity, MEDIUM
  for a THRILL ride without such a flag, LOW otherwise; DARKNESS comes from
  ``DARK_RIDE`` and WATER from ``WATER``. HEIGHTS, LOUD_NOISE and SPINNING have
  no attraction data yet, so they are not scored (not guessed).
- queue: the posted OPERATING wait (else the curated typical wait), weighted
  by ``1 - queue_tolerance``.
- walking: the walk from ``origin_node_id`` through ``ParkGraph``, weighted
  by ``1 - walking_tolerance``. The party moves together (C20), so every guest
  walks the same minutes; only the comfort differs. A walk with no known
  route is not guessed: the attraction is charged the longest walk that is
  known (so a missing route never makes it look closer) and is listed in
  ``unrouted``.
- risk: a small penalty when no live wait was read for a queued attraction.
- change: a small penalty for an attraction outside ``base_plan`` (replans,
  P0-23: minimize unnecessary plan changes).

Group utility keeps two levers apart: how much the riders enjoy it (the mean
utility among the guests eligible to ride) and section 17's "guest left
unserved" fairness penalty, ``lambda_fairness`` times the share of the party
that can't ride. Leaving someone out costs exactly that penalty, once. Hard
constraints are never score penalties: an ineligible guest neither raises nor
dilutes the riders' mean, and an attraction nobody is eligible for is left
out, so the optimizer never sees it.

A group utility can be negative, and that is intended: it means the party is
better off skipping the stop (the riders themselves dislike it, or the cost of
leaving others out outweighs the riders' enjoyment). The optimizer never adds a
non-must-do candidate with a negative utility, so such a stop drops out of the
plan rather than only down the ranking; must-dos are scheduled regardless.

Per-guest satisfaction (C20) compares what a plan gives each guest with the
best that guest could get from the same number of stops in their own eligible
set, so a child with few eligible rides is not structurally behind an adult
with many. The coefficients live in ``ScoringConfig``; a learned model may
adjust them later (section 17).
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from parkmind.core.contracts import (
    Attraction,
    AttractionCategory,
    AttractionStatus,
    GroupObjective,
    GuestProfile,
    LiveContext,
    Plan,
    RideRestriction,
    SensitivityKind,
    SensitivityLevel,
    StopKind,
)
from parkmind.services.personalization.group_preference_resolver import (
    DEFAULT_QUEUE_TOLERANCE,
    DEFAULT_WALKING_TOLERANCE,
)
from parkmind.services.planning.park_graph import ParkGraph
from parkmind.services.ports import KnowledgeStore, RoutingError

PREFERENCE_SCORER_VERSION = "preference-scorer-1"
"""Goes into ``Provenance.preference_model_version``."""

_GUEST_SENSITIVITY = {
    SensitivityLevel.LOW: 0.25,
    SensitivityLevel.MEDIUM: 0.5,
    SensitivityLevel.HIGH: 1.0,
}
_INTENSE_NOTICE_FLAGS = frozenset(
    {
        RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE,
        RideRestriction.NOT_RECOMMENDED_MOTION_SENSITIVITY,
    }
)
_SCORED_STOP_KINDS = frozenset({StopKind.ATTRACTION, StopKind.SHOW})


@dataclass(frozen=True)
class ScoringConfig:
    """Coefficients of the utility function (section 17); explicit and testable."""

    category_weight: float = 0.5
    affinity_weight: float = 0.5
    sensitivity_weight: float = 1.0
    queue_weight: float = 1.0
    """Penalty per hour of wait for a guest with zero queue tolerance."""
    walking_weight: float = 0.5
    """Penalty per 30 minutes of walking for a guest with zero walking comfort."""
    risk_penalty: float = 0.1
    change_penalty: float = 0.2

    def __post_init__(self) -> None:
        invalid = sorted(
            name for name, value in vars(self).items() if not math.isfinite(value) or value < 0
        )
        if invalid:
            raise ValueError(f"scoring coefficients must be finite and >= 0: {', '.join(invalid)}")


@dataclass(frozen=True)
class PreferenceScores:
    """What the scorer hands to the optimizer, the explainer and the metrics."""

    group: Mapping[str, float]
    """``node_id -> utility`` for the party; only attractions someone can ride."""
    per_guest: Mapping[str, Mapping[str, float]]
    """``guest_id -> node_id -> utility``, over that guest's eligible set only."""
    unmatched_affinities: tuple[str, ...] = ()
    """``guest_id:key`` for affinity keys that match no land or category in the catalog."""
    unrouted: tuple[str, ...] = ()
    """Attractions with no known walk from the origin (charged the longest known walk)."""
    model_version: str = PREFERENCE_SCORER_VERSION

    def utilities(self) -> dict[str, float]:
        """The ``utilities`` argument of ``GreedyInsertionOptimizer.build_plan``."""
        return dict(self.group)


class PreferenceScorer:
    def __init__(self, config: ScoringConfig | None = None) -> None:
        self._config = config or ScoringConfig()

    @property
    def config(self) -> ScoringConfig:
        return self._config

    def score(
        self,
        *,
        objective: GroupObjective,
        profiles: Sequence[GuestProfile],
        attractions: Sequence[Attraction],
        live_context: LiveContext,
        knowledge: KnowledgeStore,
        park_graph: ParkGraph | None = None,
        origin_node_id: str | None = None,
        base_plan: Plan | None = None,
    ) -> PreferenceScores:
        """Utility per attraction for the party in ``objective``.

        The party is the guests in ``objective.per_guest_eligible``; a profile
        for anyone else is ignored, a repeated ``guest_id`` keeps its first
        profile (as GroupPreferenceResolver does), and a guest without a
        profile gets the resolver's default tolerances and no preferences. A
        repeated attraction id is refused: two catalog entries for one node
        can't both be right.
        """
        party = sorted(objective.per_guest_eligible)
        profile_of: dict[str, GuestProfile] = {}
        for candidate in profiles:
            profile_of.setdefault(candidate.guest_id, candidate)
        counts = Counter(a.node_id for a in attractions)
        repeated = sorted(node_id for node_id, n in counts.items() if n > 1)
        if repeated:
            raise ValueError(f"attractions repeat node ids: {', '.join(repeated)}")
        catalog = {a.node_id: a for a in sorted(attractions, key=lambda a: a.node_id)}
        walks, unrouted = self._walks(catalog, park_graph, origin_node_id)
        in_base = {s.node_id for s in base_plan.stops} if base_plan is not None else None
        lands = _land_lookup(catalog.values(), park_graph)
        present = _catalog_targets(catalog.values())
        # One notice read per attraction (the port may be the pgvector adapter).
        intensity = {node_id: _intensity(a, knowledge) for node_id, a in catalog.items()}

        per_guest: dict[str, dict[str, float]] = {}
        unmatched: list[str] = []
        for guest_id in party:
            profile = profile_of.get(guest_id)
            affinity, missing = _affinities(profile, lands, present)
            unmatched.extend(f"{guest_id}:{key}" for key in missing)
            eligible = set(objective.per_guest_eligible[guest_id])
            per_guest[guest_id] = {
                node_id: self._guest_utility(
                    attraction, profile, affinity, live_context, intensity[node_id], walks, in_base
                )
                for node_id, attraction in catalog.items()
                if node_id in eligible
            }

        group: dict[str, float] = {}
        lambda_fairness = objective.fairness.lambda_fairness
        for node_id in catalog:
            riders = [g for g in party if node_id in per_guest[g]]
            if not riders:
                continue  # nobody may ride it: left out, not penalized
            mean = sum(per_guest[g][node_id] for g in riders) / len(riders)
            unserved_share = (len(party) - len(riders)) / len(party)
            group[node_id] = mean - lambda_fairness * unserved_share

        return PreferenceScores(
            group=MappingProxyType(group),
            per_guest=MappingProxyType(
                {g: MappingProxyType(utilities) for g, utilities in per_guest.items()}
            ),
            unmatched_affinities=tuple(unmatched),
            unrouted=tuple(unrouted),
        )

    def _guest_utility(
        self,
        attraction: Attraction,
        profile: GuestProfile | None,
        affinity: Mapping[str, float],
        live_context: LiveContext,
        intensity: float,
        walks: Mapping[str, float],
        in_base: set[str] | None,
    ) -> float:
        c = self._config
        utility = 1.0
        queue_tolerance = DEFAULT_QUEUE_TOLERANCE
        walking_tolerance = DEFAULT_WALKING_TOLERANCE
        if profile is not None:
            queue_tolerance = profile.queue_tolerance.value
            walking_tolerance = profile.walking_tolerance.value
            if attraction.category in profile.preferred_categories:
                utility += c.category_weight
            if attraction.category in profile.avoided_categories:
                utility -= c.category_weight
            utility += c.affinity_weight * _affinity_for(attraction, affinity)
            utility -= c.sensitivity_weight * _sensitivity_load(attraction, profile, intensity)

        wait, live_reading = _wait_minutes(attraction, live_context)
        utility -= c.queue_weight * (1.0 - queue_tolerance) * wait / 60.0
        if attraction.node_id in walks:
            utility -= c.walking_weight * (1.0 - walking_tolerance) * walks[attraction.node_id] / 30.0
        if not live_reading and attraction.category != AttractionCategory.SHOW:
            utility -= c.risk_penalty
        if in_base is not None and attraction.node_id not in in_base:
            utility -= c.change_penalty
        return utility

    @staticmethod
    def _walks(
        catalog: Mapping[str, Attraction],
        park_graph: ParkGraph | None,
        origin_node_id: str | None,
    ) -> tuple[dict[str, float], list[str]]:
        if park_graph is None or origin_node_id is None:
            return {}, []
        walks: dict[str, float] = {}
        unrouted: list[str] = []
        for node_id in catalog:
            if node_id == origin_node_id:
                walks[node_id] = 0.0
                continue
            try:
                walks[node_id] = park_graph.walk_minutes(origin_node_id, node_id)
            except RoutingError:
                unrouted.append(node_id)
        # An unrouted ride is charged the longest walk to another routed ride. If the origin is
        # the only routed node, there is no such walk: every other ride is then equally
        # unrouted and unpenalized, so none looks closer than another; they stay reported.
        routed = [minutes for node_id, minutes in walks.items() if node_id != origin_node_id]
        if routed and unrouted:
            longest = max(routed)
            walks.update({node_id: longest for node_id in unrouted})
        return walks, unrouted


def per_guest_satisfaction(plan: Plan, scores: PreferenceScores) -> dict[str, float]:
    """Satisfaction per guest in 0..1, normalized by each guest's own eligible set (C20).

    For guest g with k = the number of ATTRACTION/SHOW stops in the plan:
    the positive utility of the distinct stops g is served at, over the sum of
    g's k best positive utilities in their eligible set (or fewer, if the set
    is smaller). A guest with nothing positive to enjoy scores 1.0: there was
    nothing more the plan could give them. A guest who had options but got a
    plan with no ATTRACTION/SHOW stop (empty, or only meals and rests) scores
    0.0, so the worst plan never reads as a fair one.
    """
    stops = [s for s in plan.stops if s.kind in _SCORED_STOP_KINDS]
    k = len({s.node_id for s in stops})
    satisfaction: dict[str, float] = {}
    for guest_id, utilities in scores.per_guest.items():
        positive = sorted((u for u in utilities.values() if u > 0), reverse=True)
        if not positive:
            satisfaction[guest_id] = 1.0
            continue
        if k == 0:
            satisfaction[guest_id] = 0.0
            continue
        served = {s.node_id for s in stops if guest_id in s.served_guests}
        got = sum(max(utilities.get(node_id, 0.0), 0.0) for node_id in served)
        satisfaction[guest_id] = min(got / sum(positive[:k]), 1.0)
    return satisfaction


def fairness_gap(satisfaction: Mapping[str, float]) -> float:
    """Spread between the best- and worst-served guest (section 45)."""
    return max(satisfaction.values()) - min(satisfaction.values()) if satisfaction else 0.0


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _land_lookup(attractions: Iterable[Attraction], park_graph: ParkGraph | None) -> dict[str, str]:
    """Normalized land name, land alias or category -> the key affinities are matched on."""
    lookup: dict[str, str] = {}
    for attraction in attractions:
        if attraction.land:
            lookup[_normalize(attraction.land)] = _land_key(attraction.land)
    if park_graph is not None:
        for alias, land in park_graph.land_aliases.items():
            lookup.setdefault(_normalize(alias), _land_key(land))
    for category in AttractionCategory:
        lookup.setdefault(_normalize(category.value), f"category:{category.value}")
        lookup.setdefault(_normalize(category.value.replace("_", " ")), f"category:{category.value}")
    return lookup


def _land_key(land: str) -> str:
    return f"land:{_normalize(land)}"


def _catalog_targets(attractions: Iterable[Attraction]) -> frozenset[str]:
    """Every ``land:``/``category:`` key some attraction in the catalog can be matched on."""
    targets = {f"category:{a.category.value}" for a in attractions}
    targets |= {_land_key(a.land) for a in attractions if a.land}
    return frozenset(targets)


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _affinities(
    profile: GuestProfile | None, lookup: Mapping[str, str], present: frozenset[str]
) -> tuple[dict[str, float], list[str]]:
    """The guest's affinities keyed by ``land:``/``category:``, and the keys that match no attraction."""
    if profile is None:
        return {}, []
    totals: dict[str, float] = {}
    missing: list[str] = []
    for key in sorted(profile.thematic_affinity):
        target = lookup.get(_normalize(key))
        if target is None or target not in present:
            missing.append(key)
        else:
            totals[target] = totals.get(target, 0.0) + profile.thematic_affinity[key].value
    # Sum every key that reaches a target, then clamp once: the order of the keys never matters.
    return {target: _clamp(total) for target, total in totals.items()}, missing


def _affinity_for(attraction: Attraction, affinity: Mapping[str, float]) -> float:
    total = affinity.get(f"category:{attraction.category.value}", 0.0)
    if attraction.land:
        total += affinity.get(_land_key(attraction.land), 0.0)
    return _clamp(total)


def _intensity(attraction: Attraction, knowledge: KnowledgeStore) -> float:
    notice = knowledge.notice_for(attraction.node_id)
    if notice and notice & _INTENSE_NOTICE_FLAGS:
        return 1.0
    return 0.5 if attraction.category == AttractionCategory.THRILL else 0.0


def _sensitivity_load(attraction: Attraction, profile: GuestProfile, intensity: float) -> float:
    """Sum over the guest's sensitivities of (guest level x attraction level)."""
    levels = {
        SensitivityKind.INTENSITY: intensity,
        SensitivityKind.DARKNESS: float(attraction.category == AttractionCategory.DARK_RIDE),
        SensitivityKind.WATER: float(attraction.category == AttractionCategory.WATER),
    }
    return sum(
        _GUEST_SENSITIVITY[level] * levels[kind]
        for kind, level in sorted(profile.sensitivities.items())
        if kind in levels
    )


def _wait_minutes(attraction: Attraction, live_context: LiveContext) -> tuple[float, bool]:
    """The posted OPERATING wait, else the curated typical wait; and whether it was live."""
    estimate = live_context.waits.get(attraction.node_id)
    if estimate is not None and estimate.status == AttractionStatus.OPERATING:
        return estimate.wait_minutes, True
    return float(attraction.typical_wait_minutes), False
