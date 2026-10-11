"""Keyword fallback and embedders of the knowledge store (P0-26), no database."""

import math
from pathlib import Path

import pytest

from parkmind.services.clients.knowledge.embeddings import (
    FastEmbedEmbedder,
    HashingEmbedder,
)
from parkmind.services.clients.knowledge.keyword_search import keyword_knowledge_search
from parkmind.services.ports import KnowledgeUnavailableError

SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"


def test_keyword_search_finds_the_policy_by_its_words() -> None:
    hits = keyword_knowledge_search().search_policies(
        "Rider Switch turnarse niños", k=3
    )

    assert hits and "Rider Switch" in hits[0].chunk.title
    assert hits[0].score == 1.0  # normalized to the best match
    assert all(h.chunk.kind != "attraction_profile" for h in hits)


def test_keyword_search_ignores_accents_and_case() -> None:
    search = keyword_knowledge_search()

    with_accents = search.search_policies("ESTATURA MÍNIMA Space Mountain", k=5)
    without = search.search_policies("estatura minima space mountain", k=5)

    assert [h.chunk.chunk_id for h in with_accents] == [
        h.chunk.chunk_id for h in without
    ]


def test_keyword_similar_attractions_excludes_the_reference() -> None:
    hits = keyword_knowledge_search().similar_attractions(
        attraction_id=BIG_THUNDER, text=None, k=5
    )

    assert hits
    assert all(h.chunk.kind == "attraction_profile" for h in hits)
    assert BIG_THUNDER not in {h.chunk.attraction_id for h in hits}


def test_keyword_search_is_deterministic() -> None:
    first = keyword_knowledge_search().search_policies("silla de ruedas", k=5)
    second = keyword_knowledge_search().search_policies("silla de ruedas", k=5)

    assert [(h.chunk.chunk_id, h.score) for h in first] == [
        (h.chunk.chunk_id, h.score) for h in second
    ]


def test_hashing_embedder_is_deterministic_normalized_and_accent_blind() -> None:
    embedder = HashingEmbedder()

    a, b = embedder.embed_documents(["Montaña rusa", "montana RUSA"])

    assert a == b
    assert len(a) == embedder.dimension == 384
    assert math.isclose(sum(x * x for x in a), 1.0)
    assert embedder.embedder_id == "hashing-v1@384"
    assert HashingEmbedder(salt="v2").embed_query("Montaña rusa") != a


def test_fastembed_with_an_empty_cache_is_unavailable_and_never_downloads(
    tmp_path: Path,
) -> None:
    embedder = FastEmbedEmbedder(cache_dir=tmp_path)

    assert embedder.available() is False
    with pytest.raises(KnowledgeUnavailableError, match="fetch_embedding_model"):
        embedder.embed_query("hola")
    assert not any(p.suffix == ".onnx" for p in tmp_path.rglob("*"))
