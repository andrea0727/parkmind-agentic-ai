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

Each query opens only what it reads. The notices are in memory, so checks do no
I/O at all; semantic search opens a database connection of its own
(``default_knowledge_search``) and never the planner's ports, so a provider or a
database that is down costs search its semantic ranking and nothing else.
"""

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from parkmind.config.settings import settings
from parkmind.core.contracts import AccessibilityCheck, RideRestriction
from parkmind.services.clients.knowledge import magic_kingdom_knowledge_store
from parkmind.services.clients.knowledge.embeddings import FastEmbedEmbedder
from parkmind.services.clients.knowledge.keyword_search import keyword_knowledge_search
from parkmind.services.clients.knowledge.pgvector_store import PgvectorKnowledgeSearch
from parkmind.services.clients.postgres import connect
from parkmind.services.clients.postgres.connection import DATABASE_PROBLEMS
from parkmind.services.ports import (
    KnowledgeHit,
    KnowledgeSearch,
    KnowledgeStore,
    KnowledgeUnavailableError,
)
from parkmind.services.use_cases.check_accessibility import check_flags

__all__ = [
    "AccessibilityAnswer",
    "KnowledgeHit",
    "KnowledgeQueries",
    "KnowledgeSearchFactory",
    "SearchAnswer",
    "SimilarAnswer",
    "SimilarAttraction",
    "UnknownAttractionError",
    "default_knowledge_search",
    "intensity",
]

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


KnowledgeSearchFactory = Callable[[], AbstractContextManager[KnowledgeSearch | None]]
"""Opens semantic search for one query; yields ``None`` when it is not configured."""


class UnknownAttractionError(LookupError):
    """The attraction has no profile in the knowledge corpus."""


@lru_cache(maxsize=1)
def _shared_embedder() -> FastEmbedEmbedder:
    """One embedding model per process: loading it per call would cost a second each time."""
    return FastEmbedEmbedder(
        settings.EMBEDDING_MODEL, cache_dir=settings.EMBEDDING_CACHE
    )


@lru_cache(maxsize=1)
def _keyword_fallback() -> KnowledgeSearch:
    return keyword_knowledge_search()


@contextmanager
def default_knowledge_search() -> Iterator[KnowledgeSearch | None]:
    """pgvector on its own connection; ``PARKMIND_KNOWLEDGE_BACKEND=in_memory`` keeps
    keyword search only. A database that is down makes search unavailable (section 43)."""
    if settings.KNOWLEDGE_BACKEND != "pgvector":
        yield None
        return
    try:
        with connect() as conn:
            yield PgvectorKnowledgeSearch(conn, _shared_embedder())
    except DATABASE_PROBLEMS as exc:
        raise KnowledgeUnavailableError(f"database: {type(exc).__name__}") from exc


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
        *,
        search: KnowledgeSearchFactory = default_knowledge_search,
        notices: KnowledgeStore | None = None,
        fallback: KnowledgeSearch | None = None,
    ) -> None:
        self._search = search
        self._notices = (
            notices if notices is not None else magic_kingdom_knowledge_store()
        )
        self._fallback = fallback if fallback is not None else _keyword_fallback()

    def search_policies(self, query: str, k: int) -> SearchAnswer:
        try:
            with self._search() as primary:
                if primary is not None:
                    hits = primary.search_policies(query, k)
                    return SearchAnswer(
                        hits, primary.corpus_version, primary.strategy, None
                    )
            reason = "semantic_search_not_configured"
        except KnowledgeUnavailableError as exc:
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
        notices = self._notices
        reference = intensity(notices, attraction_id) if attraction_id else None
        hits, search, degraded = self._similar(attraction_id, text, k)
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
        attraction_id: str | None,
        text: str | None,
        k: int,
    ) -> tuple[list[KnowledgeHit], KnowledgeSearch, str | None]:
        wanted = k * _CANDIDATES_PER_RESULT
        try:
            with self._search() as primary:
                if primary is not None:
                    hits = primary.similar_attractions(
                        attraction_id=attraction_id, text=text, k=wanted
                    )
                    return hits, primary, None
            reason = "semantic_search_not_configured"
        except KnowledgeUnavailableError as exc:
            logger.warning(
                "find_similar_attractions degraded to keyword search: %s", exc
            )
            reason = "semantic_search_unavailable"
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
        derived = frozenset(flags)
        checks = [
            check_flags(guest_id, derived, a, self._notices)
            for a in dict.fromkeys(attraction_ids)
        ]
        return AccessibilityAnswer(checks, self._notices.corpus_version)
