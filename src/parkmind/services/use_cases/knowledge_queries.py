"""Use case: the reads behind the ``knowledge.*`` tools (P0-26; Architecture section 30).

* ``search_policies`` -- passages of the reviewed corpus (policies, accessibility
  information) closest to a question: semantic search when it can answer, else
  the keyword fallback over the same passages, with the reason named
  (section 43: "retrieval -> static rules / known metadata"). Never an error for
  a missing model or index: the answer says it was degraded.
* ``find_similar_attractions`` -- attraction profiles closest to an attraction
  or a description, optionally only the *less intense* ones. Intensity is read
  from the reviewed safety notices, deterministically: ``high`` where the park
  publishes its full rider warning (``NOT_RECOMMENDED_HIGH_G_FORCE``),
  ``moderate`` for a motion, back/neck or heart warning, ``low`` for none,
  ``unknown`` without a notice. These tiers are provisional: the notice corpus
  does not grade intensity (an open product question, P0-26a), so "less intense"
  keeps only attractions of a strictly lower tier, never an ``unknown`` one.
* ``check_accessibility`` -- delegates to ``check_flags`` (P0-26a) and keeps its
  fail-closed result. It never depends on retrieval and never degrades.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from parkmind.core.contracts import AccessibilityCheck, RideRestriction
from parkmind.services.clients.knowledge.keyword_search import keyword_knowledge_search
from parkmind.services.ports import (
    KnowledgeHit,
    KnowledgeSearch,
    KnowledgeStore,
    KnowledgeUnavailableError,
)
from parkmind.services.use_cases.check_accessibility import check_flags
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    PlanningUnavailableError,
    default_planning_deps,
    open_deps,
)

logger = logging.getLogger(__name__)

Intensity = Literal["low", "moderate", "high", "unknown"]
_TIER: dict[Intensity, int] = {"low": 0, "moderate": 1, "high": 2}
_MODERATE = frozenset(
    {
        RideRestriction.NOT_RECOMMENDED_MOTION_SENSITIVITY,
        RideRestriction.NOT_RECOMMENDED_BACK_NECK,
        RideRestriction.NOT_RECOMMENDED_HEART_CONDITION,
    }
)
_CANDIDATES_PER_RESULT = (
    4  # fetched per requested result, so filtering can still fill k
)


class UnknownAttractionError(LookupError):
    """The attraction has no profile in the knowledge corpus."""


@dataclass(frozen=True)
class SearchAnswer:
    hits: list[KnowledgeHit]
    corpus_version: str
    strategy: str
    degraded: str | None
    """Why the keyword fallback answered (section 43), or ``None``."""


@dataclass(frozen=True)
class SimilarAttraction:
    hit: KnowledgeHit
    intensity: Intensity


@dataclass(frozen=True)
class SimilarAnswer:
    attractions: list[SimilarAttraction]
    reference_intensity: Intensity | None
    corpus_version: str
    strategy: str
    degraded: str | None


@dataclass(frozen=True)
class AccessibilityAnswer:
    checks: list[AccessibilityCheck]
    notice_corpus_version: str


def intensity(store: KnowledgeStore, attraction_id: str) -> Intensity:
    notice = store.notice_for(attraction_id)
    if notice is None:
        return "unknown"
    if RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE in notice:
        return "high"
    if notice & _MODERATE:
        return "moderate"
    return "low"


class KnowledgeQueries:
    def __init__(
        self,
        deps_factory: DepsFactory = default_planning_deps,
        *,
        fallback: KnowledgeSearch | None = None,
    ) -> None:
        self._deps_factory = deps_factory
        self._fallback = (
            fallback if fallback is not None else keyword_knowledge_search()
        )

    def search_policies(self, query: str, k: int) -> SearchAnswer:
        try:
            with open_deps(self._deps_factory) as deps:
                primary = deps.knowledge_search
                if primary is not None:
                    hits = primary.search_policies(query, k)
                    return SearchAnswer(
                        hits, primary.corpus_version, primary.strategy, None
                    )
            reason = "semantic_search_not_configured"
        except (KnowledgeUnavailableError, PlanningUnavailableError) as exc:
            logger.warning("search_policies degraded to keyword search: %s", exc)
            reason = "semantic_search_unavailable"
        hits = self._fallback.search_policies(query, k)
        return SearchAnswer(
            hits, self._fallback.corpus_version, self._fallback.strategy, reason
        )

    def find_similar_attractions(
        self,
        *,
        attraction_id: str | None,
        text: str | None,
        k: int,
        less_intense: bool = False,
    ) -> SimilarAnswer:
        if (attraction_id is None) == (text is None):
            raise ValueError("give exactly one of attraction_id or text")
        with open_deps(self._deps_factory) as deps:
            notices = deps.knowledge
            primary = deps.knowledge_search
            reference = intensity(notices, attraction_id) if attraction_id else None
            hits, search, degraded = self._similar(primary, attraction_id, text, k)
        if (
            attraction_id is not None
            and not hits
            and not self._fallback.similar_attractions(
                attraction_id=attraction_id, text=None, k=1
            )
        ):
            raise UnknownAttractionError(f"no profile for attraction {attraction_id!r}")
        candidates = [
            SimilarAttraction(h, intensity(notices, h.chunk.attraction_id or ""))
            for h in hits
        ]
        if less_intense:
            ceiling = (
                _TIER.get(reference, 0) if reference else 1
            )  # text query: only "low"
            candidates = [
                c
                for c in candidates
                if c.intensity in _TIER and _TIER[c.intensity] < ceiling
            ]
        return SimilarAnswer(
            candidates[:k], reference, search.corpus_version, search.strategy, degraded
        )

    def _similar(
        self,
        primary: KnowledgeSearch | None,
        attraction_id: str | None,
        text: str | None,
        k: int,
    ) -> tuple[list[KnowledgeHit], KnowledgeSearch, str | None]:
        wanted = k * _CANDIDATES_PER_RESULT
        if primary is not None:
            try:
                return (
                    primary.similar_attractions(
                        attraction_id=attraction_id, text=text, k=wanted
                    ),
                    primary,
                    None,
                )
            except KnowledgeUnavailableError as exc:
                logger.warning(
                    "find_similar_attractions degraded to keyword search: %s", exc
                )
                reason = "semantic_search_unavailable"
        else:
            reason = "semantic_search_not_configured"
        hits = self._fallback.similar_attractions(
            attraction_id=attraction_id, text=text, k=wanted
        )
        return hits, self._fallback, reason

    def check_accessibility(
        self,
        attraction_ids: Sequence[str],
        flags: Sequence[RideRestriction],
        guest_id: str = "guest",
    ) -> AccessibilityAnswer:
        with open_deps(self._deps_factory) as deps:
            store = deps.knowledge
            derived = frozenset(flags)
            checks = [
                check_flags(guest_id, derived, a, store)
                for a in dict.fromkeys(attraction_ids)
            ]
            return AccessibilityAnswer(checks, store.corpus_version)
