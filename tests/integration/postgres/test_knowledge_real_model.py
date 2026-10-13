"""Semantic search with the real multilingual model (P0-26), marked ``requires_model``.

English questions against the Spanish corpus. Skipped unless the model is in
the persistent cache (``scripts/fetch_embedding_model.py``); PR CI never
downloads it -- the manual ``embedding-smoke`` workflow runs these. The model
is a deterministic ONNX run, so the rankings asserted below are stable.

Known miss, not asserted: a paraphrase such as "Can I wait with my baby while my
partner rides?" does not reach the Rider Switch passages with this model.
"""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from parkmind.config.settings import settings
from parkmind.services.clients.knowledge.embeddings import FastEmbedEmbedder
from parkmind.services.clients.knowledge.knowledge_corpus import knowledge_corpus
from parkmind.services.clients.knowledge.pgvector_store import PgvectorKnowledgeSearch

SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
TRON = "5a43d1a7-ad53-4d25-abfe-25625f0da304"

MODEL = FastEmbedEmbedder(settings.EMBEDDING_MODEL, cache_dir=settings.EMBEDDING_CACHE)

pytestmark = [
    pytest.mark.requires_model,
    pytest.mark.skipif(
        not MODEL.available(),
        reason="embedding model not cached; run scripts/fetch_embedding_model.py",
    ),
]


@pytest.fixture(scope="module")
def search(migrated_database_url: str) -> Iterator[PgvectorKnowledgeSearch]:
    with psycopg.connect(migrated_database_url, row_factory=dict_row) as conn:
        conn.execute("TRUNCATE knowledge_chunks")
        store = PgvectorKnowledgeSearch(conn, MODEL)
        store.index(knowledge_corpus())
        conn.commit()
        yield store
        conn.execute("TRUNCATE knowledge_chunks")
        conn.commit()


def test_an_english_height_question_finds_the_spanish_height_rule(
    search: PgvectorKnowledgeSearch,
) -> None:
    top = search.search_policies("Can a 100 cm child ride Space Mountain?", k=3)[0]

    assert top.chunk.attraction_id == SPACE_MOUNTAIN
    assert "estatura" in top.chunk.body


@pytest.mark.parametrize(
    ("question", "title_word"),
    [
        ("Are service animals allowed on rides?", "animales de servicio"),
        ("Where can I rent a stroller?", "cochecitos"),
        ("first aid nurse", "Primeros auxilios"),
    ],
)
def test_english_policy_questions_reach_the_right_policy(
    search: PgvectorKnowledgeSearch, question: str, title_word: str
) -> None:
    titles = [hit.chunk.title for hit in search.search_policies(question, k=3)]

    assert any(title_word.lower() in t.lower() for t in titles), titles


def test_a_description_finds_the_dark_coasters(search: PgvectorKnowledgeSearch) -> None:
    hits = search.similar_attractions(
        attraction_id=None, text="a fast roller coaster in the dark", k=3
    )

    assert {SPACE_MOUNTAIN, TRON} <= {h.chunk.attraction_id for h in hits}
