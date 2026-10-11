"""The pgvector knowledge store on a real Postgres (P0-26).

Indexed with the deterministic ``HashingEmbedder`` (lexical, no model download),
so the rankings below are stable on every machine and in CI. The real
multilingual model is exercised by the ``requires_model`` tests.
"""

import psycopg
import pytest

from parkmind.services.clients.knowledge.embeddings import HashingEmbedder
from parkmind.services.clients.knowledge.knowledge_corpus import knowledge_corpus
from parkmind.services.clients.knowledge.pgvector_store import PgvectorKnowledgeSearch
from parkmind.services.ports import KnowledgeUnavailableError

SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"


@pytest.fixture
def indexed(conn: psycopg.Connection) -> PgvectorKnowledgeSearch:
    search = PgvectorKnowledgeSearch(conn, HashingEmbedder())
    search.index(knowledge_corpus())
    conn.commit()
    return search


def _rows(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT count(*) AS n FROM knowledge_chunks").fetchone()
    assert row is not None
    return int(row["n"])


def test_index_is_idempotent_per_version(conn: psycopg.Connection) -> None:
    chunks = knowledge_corpus()
    search = PgvectorKnowledgeSearch(conn, HashingEmbedder())

    first = search.index(chunks)
    second = search.index(chunks)
    other_model = PgvectorKnowledgeSearch(conn, HashingEmbedder(salt="v2")).index(
        chunks
    )

    assert (first.inserted, first.already_indexed) == (len(chunks), 0)
    assert (second.inserted, second.already_indexed) == (0, len(chunks))
    assert other_model.inserted == len(chunks)  # another embedder keeps its own rows
    assert _rows(conn) == 2 * len(chunks)


def test_search_policies_ranks_relevant_chunk(indexed: PgvectorKnowledgeSearch) -> None:
    """Lexical (hashing) ranking: the height passages come first, Space Mountain's among them.

    A short passage dense in the query's words (TRON's one-sentence height rule)
    can outrank Space Mountain's longer one under a lexical embedder; the
    ``requires_model`` tests check the semantic ranking.
    """
    hits = indexed.search_policies("requisito mínimo de estatura Space Mountain", k=3)

    assert "estatura" in hits[0].chunk.body
    assert SPACE_MOUNTAIN in {hit.chunk.attraction_id for hit in hits}
    assert all(hit.chunk.kind != "attraction_profile" for hit in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_similar_attractions_ranks_profiles_and_excludes_the_reference(
    indexed: PgvectorKnowledgeSearch,
) -> None:
    hits = indexed.similar_attractions(attraction_id=BIG_THUNDER, text=None, k=5)

    assert len(hits) == 5
    assert all(h.chunk.kind == "attraction_profile" for h in hits)
    assert BIG_THUNDER not in {h.chunk.attraction_id for h in hits}


def test_an_attraction_without_a_profile_has_no_similar_ones(
    indexed: PgvectorKnowledgeSearch,
) -> None:
    assert indexed.similar_attractions(attraction_id="no-such", text=None, k=5) == []


def test_mismatched_embedder_degrades(
    indexed: PgvectorKnowledgeSearch, conn: psycopg.Connection
) -> None:
    """Vectors of another model are never compared: no rows for it means unavailable."""
    other = PgvectorKnowledgeSearch(conn, HashingEmbedder(salt="another-model"))

    with pytest.raises(KnowledgeUnavailableError, match="no index"):
        other.search_policies("estatura", k=3)


class _BrokenQueryEmbedder(HashingEmbedder):
    """Same id as the index, but answers queries with a vector of the wrong size."""

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def test_retrieval_failure_leaves_guest_state_intact(
    indexed: PgvectorKnowledgeSearch, conn: psycopg.Connection
) -> None:
    """P0-26 Done-when: a failed search inside an open transaction must not undo it."""
    conn.execute("INSERT INTO guests (guest_id, role) VALUES ('g1', 'adult')")
    broken = PgvectorKnowledgeSearch(conn, _BrokenQueryEmbedder())

    with pytest.raises(KnowledgeUnavailableError, match="query failed"):
        broken.search_policies("estatura", k=3)
    conn.commit()

    row = conn.execute(
        "SELECT count(*) AS n FROM guests WHERE guest_id = 'g1'"
    ).fetchone()
    assert row is not None and row["n"] == 1


def test_knowledge_tools_answer_from_the_index_over_mcp(
    indexed: PgvectorKnowledgeSearch,
) -> None:
    """The whole path: MCP call -> KnowledgeQueries -> pgvector, no degradation."""
    from mcp_support import Deps, call_tool

    registry = Deps(collect_at=None, knowledge_search=indexed).registry()

    search = call_tool(
        registry,
        "knowledge.search_policies",
        {"query": "requisito mínimo de estatura", "k": 3},
    ).structured_content
    similar = call_tool(
        registry,
        "knowledge.find_similar_attractions",
        {"attraction_id": BIG_THUNDER, "k": 3, "less_intense": True},
    ).structured_content

    assert search["provenance"]["strategy"] == "semantic:hashing-v1@384"
    assert search["provenance"]["degraded"] is None
    assert "estatura" in search["data"]["passages"][0]["text"]
    assert similar["provenance"]["strategy"] == "semantic:hashing-v1@384"
    assert similar["data"]["attractions"]
    assert {a["intensity"] for a in similar["data"]["attractions"]} <= {
        "low",
        "moderate",
    }
