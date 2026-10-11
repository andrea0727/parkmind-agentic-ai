"""KnowledgeQueries (P0-26): semantic when it can, keyword when it can't, and
accessibility checks that never degrade."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from mcp_support import fixed_search

from parkmind.config.settings import settings
from parkmind.core.contracts import RideRestriction
from parkmind.services.clients.knowledge import magic_kingdom_knowledge_store
from parkmind.services.clients.knowledge.knowledge_corpus import knowledge_corpus
from parkmind.services.ports import KnowledgeHit, KnowledgeUnavailableError
from parkmind.services.use_cases import knowledge_queries
from parkmind.services.use_cases.check_accessibility import check_flags
from parkmind.services.use_cases.knowledge_queries import (
    KnowledgeQueries,
    UnknownAttractionError,
    intensity,
)

BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"
SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
SMALL_WORLD = "f5aad2d4-a419-4384-bd9a-42f86385c750"


class _Semantic:
    """A semantic search that answers with a fixed passage, or is unavailable."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable

    corpus_version = "test-corpus"
    strategy = "semantic:test-model"

    def search_policies(self, query: str, k: int) -> list[KnowledgeHit]:
        if self.unavailable:
            raise KnowledgeUnavailableError("model not in cache")
        return [KnowledgeHit(knowledge_corpus()[0], 0.9)]

    def similar_attractions(
        self, *, attraction_id: Any, text: Any, k: int
    ) -> list[KnowledgeHit]:
        if self.unavailable:
            raise KnowledgeUnavailableError("no index")
        return []


def _queries(knowledge_search: Any = None) -> KnowledgeQueries:
    return KnowledgeQueries(search=fixed_search(knowledge_search))


def test_search_policies_uses_semantic_search_when_it_answers() -> None:
    answer = _queries(_Semantic()).search_policies("estatura", k=3)

    assert answer.strategy == "semantic:test-model"
    assert answer.corpus_version == "test-corpus"
    assert answer.degraded is None
    assert len(answer.hits) == 1


def test_search_policies_degrades_without_model() -> None:
    """Section 43: retrieval unavailable -> keyword search over the same corpus, said so."""
    answer = _queries(_Semantic(unavailable=True)).search_policies("Rider Switch", k=3)

    assert answer.strategy == "keyword"
    assert answer.degraded == "semantic_search_unavailable"
    assert answer.hits and "Rider Switch" in answer.hits[0].chunk.title


def test_search_policies_without_semantic_search_configured_is_keyword() -> None:
    answer = _queries(None).search_policies("Rider Switch", k=3)

    assert answer.strategy == "keyword"
    assert answer.degraded == "semantic_search_not_configured"


@pytest.fixture
def database_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default semantic search, with a database that refuses connections."""

    @contextmanager
    def refused() -> Iterator[Any]:
        raise psycopg.OperationalError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr(settings, "KNOWLEDGE_BACKEND", "pgvector")
    monkeypatch.setattr(knowledge_queries, "connect", refused)


@pytest.mark.usefixtures("database_down")
def test_search_policies_degrades_when_the_database_is_down() -> None:
    answer = KnowledgeQueries().search_policies("Rider Switch", k=3)

    assert answer.degraded == "semantic_search_unavailable" and answer.hits


@pytest.mark.usefixtures("database_down")
def test_find_similar_degrades_when_the_database_is_down() -> None:
    """Section 43 holds for both searches, not only search_policies."""
    answer = KnowledgeQueries().find_similar_attractions(
        attraction_id=BIG_THUNDER, text=None, k=3, less_intense=True
    )

    assert answer.strategy == "keyword"
    assert answer.degraded == "semantic_search_unavailable"
    assert answer.attractions and answer.reference_intensity == "high"


@pytest.mark.usefixtures("database_down")
def test_check_accessibility_needs_no_database() -> None:
    answer = KnowledgeQueries().check_accessibility(
        [BIG_THUNDER, SMALL_WORLD], [RideRestriction.REQUIRES_TRANSFER_FROM_WHEELCHAIR]
    )

    assert [c.eligible for c in answer.checks] == [False, True]


def test_find_similar_less_intense_drops_high_g() -> None:
    queries = _queries(None)

    every = queries.find_similar_attractions(attraction_id=BIG_THUNDER, text=None, k=6)
    gentler = queries.find_similar_attractions(
        attraction_id=BIG_THUNDER, text=None, k=6, less_intense=True
    )

    assert every.reference_intensity == "high"
    assert "high" in {a.intensity for a in every.attractions}
    assert gentler.attractions and len(gentler.attractions) == 6
    assert {a.intensity for a in gentler.attractions} <= {"low", "moderate"}
    assert SPACE_MOUNTAIN not in {
        a.hit.chunk.attraction_id for a in gentler.attractions
    }
    assert BIG_THUNDER not in {a.hit.chunk.attraction_id for a in every.attractions}


def test_find_similar_by_text_less_intense_keeps_only_low_tier() -> None:
    answer = _queries(None).find_similar_attractions(
        attraction_id=None, text="montaña rusa a toda velocidad", k=5, less_intense=True
    )

    assert answer.attractions
    assert {a.intensity for a in answer.attractions} <= {"low"}


def test_find_similar_for_an_attraction_without_a_profile_is_unknown() -> None:
    with pytest.raises(UnknownAttractionError):
        _queries(None).find_similar_attractions(attraction_id="no-such", text=None, k=3)


def test_find_similar_needs_exactly_one_reference() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _queries(None).find_similar_attractions(
            attraction_id=BIG_THUNDER, text="x", k=3
        )


def test_check_accessibility_delegates_to_check_flags_and_never_degrades() -> None:
    flags = [RideRestriction.REQUIRES_TRANSFER_FROM_WHEELCHAIR]
    store = magic_kingdom_knowledge_store()

    answer = _queries(_Semantic(unavailable=True)).check_accessibility(
        [BIG_THUNDER, SMALL_WORLD, "no-such"], flags, guest_id="g4"
    )

    expected = [
        check_flags("g4", frozenset(flags), a, store)
        for a in (BIG_THUNDER, SMALL_WORLD, "no-such")
    ]
    assert answer.checks == expected
    assert answer.checks[0].eligible is False  # transfer required
    assert answer.checks[1].eligible is True  # "it's a small world": no transfer needed
    assert answer.checks[2].eligible is False  # no notice on file: fail closed
    assert answer.notice_corpus_version == store.corpus_version


def test_intensity_tiers_come_from_the_safety_notices() -> None:
    store = magic_kingdom_knowledge_store()

    assert intensity(store, BIG_THUNDER) == "high"
    assert intensity(store, SMALL_WORLD) == "low"
    assert intensity(store, "no-such") == "unknown"
