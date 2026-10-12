"""Semantic search over pgvector (P0-26; Architecture section 30 [C21], migration 0003).

Implements ``KnowledgeSearch``: passages of the reviewed corpus ranked by cosine
distance between their stored embedding and the query's. Rows are keyed by
``(corpus_version, embedder_id, chunk_id)``: a search only reads rows of the
current corpus version **and** of the embedder it was given, so vectors from
different models are never compared -- no such rows means
``KnowledgeUnavailableError`` (re-index, or degrade to keyword search).

Each query runs in its own savepoint (``conn.transaction()``): a retrieval
failure rolls back only that savepoint, never the caller's open transaction,
so it cannot corrupt guest state written on the same connection (P0-26
Done-when). Every failure surfaces as ``KnowledgeUnavailableError``.

Search is exact nearest-neighbour, by design (ADR 0002). Measured with
``EXPLAIN ANALYZE`` on the 134-passage corpus (pgvector 0.8.7): the rows of this
corpus and embedder are read by a sequential scan and sorted exactly in under a
millisecond; ties are broken by ``chunk_id``, so the same question always ranks
the same way. An approximate HNSW index would not be used at this size and
cannot serve that tie-break.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row

from parkmind.services.ports import (
    KnowledgeChunk,
    KnowledgeHit,
    KnowledgeUnavailableError,
)

from .embeddings import Embedder
from .knowledge_corpus import KNOWLEDGE_CORPUS_VERSION

_INDEX_BATCH = 32
_COLUMNS = "chunk_id, kind, attraction_id, title, body, source_url, reviewed_on"


def vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"


@dataclass(frozen=True)
class IndexReport:
    corpus_version: str
    embedder_id: str
    inserted: int
    already_indexed: int


class PgvectorKnowledgeSearch:
    def __init__(
        self,
        conn: psycopg.Connection[Any],
        embedder: Embedder,
        *,
        corpus_version: str = KNOWLEDGE_CORPUS_VERSION,
    ) -> None:
        self._conn = conn
        self._embedder = embedder
        self._corpus_version = corpus_version

    @property
    def corpus_version(self) -> str:
        return self._corpus_version

    @property
    def strategy(self) -> str:
        return f"semantic:{self._embedder.embedder_id}"

    # -- indexing -----------------------------------------------------------------------

    def index(self, chunks: Sequence[KnowledgeChunk]) -> IndexReport:
        """Embed and store every chunk not yet indexed for this corpus and embedder."""
        with self._conn.transaction(), self._conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT chunk_id FROM knowledge_chunks WHERE corpus_version = %s AND embedder_id = %s",
                (self._corpus_version, self._embedder.embedder_id),
            )
            present = {row[0] for row in cur.fetchall()}
            missing = [c for c in chunks if c.chunk_id not in present]
            for start in range(0, len(missing), _INDEX_BATCH):
                batch = missing[start : start + _INDEX_BATCH]
                vectors = self._embedder.embed_documents(
                    [_embedding_text(c) for c in batch]
                )
                cur.executemany(
                    "INSERT INTO knowledge_chunks (corpus_version, embedder_id, chunk_id, kind, "
                    "attraction_id, title, body, source_url, reviewed_on, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector) "
                    "ON CONFLICT DO NOTHING",
                    [
                        (
                            self._corpus_version,
                            self._embedder.embedder_id,
                            c.chunk_id,
                            c.kind,
                            c.attraction_id,
                            c.title,
                            c.body,
                            c.source_url,
                            c.reviewed_on,
                            vector_literal(v),
                        )
                        for c, v in zip(batch, vectors, strict=True)
                    ],
                )
        return IndexReport(
            self._corpus_version,
            self._embedder.embedder_id,
            inserted=len(missing),
            already_indexed=len(present),
        )

    # -- search -------------------------------------------------------------------------

    def search_policies(self, query: str, k: int) -> list[KnowledgeHit]:
        vector = vector_literal(self._embedder.embed_query(query))
        return self._ranked("kind <> 'attraction_profile'", (), vector, k)

    def similar_attractions(
        self, *, attraction_id: str | None, text: str | None, k: int
    ) -> list[KnowledgeHit]:
        if attraction_id is not None:
            vector = self._profile_vector(attraction_id)
            if vector is None:
                return []
            return self._ranked(
                "kind = 'attraction_profile' AND attraction_id <> %s",
                (attraction_id,),
                vector,
                k,
            )
        vector = vector_literal(self._embedder.embed_query(text or ""))
        return self._ranked("kind = 'attraction_profile'", (), vector, k)

    def _profile_vector(self, attraction_id: str) -> str | None:
        rows = self._query(
            "SELECT embedding::text AS embedding FROM knowledge_chunks WHERE corpus_version = %s "
            "AND embedder_id = %s AND kind = 'attraction_profile' AND attraction_id = %s",
            (self._corpus_version, self._embedder.embedder_id, attraction_id),
        )
        return rows[0]["embedding"] if rows else None

    def _ranked(
        self, where: str, where_params: tuple[Any, ...], vector: str, k: int
    ) -> list[KnowledgeHit]:
        rows = self._query(
            f"SELECT {_COLUMNS}, 1 - (embedding <=> %s::vector) AS score FROM knowledge_chunks "
            f"WHERE corpus_version = %s AND embedder_id = %s AND {where} "
            "ORDER BY embedding <=> %s::vector, chunk_id LIMIT %s",
            (
                vector,
                self._corpus_version,
                self._embedder.embedder_id,
                *where_params,
                vector,
                k,
            ),
        )
        return [
            KnowledgeHit(_chunk(row), round(float(row["score"]), 6)) for row in rows
        ]

    def _query(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        try:
            with (
                self._conn.transaction(),
                self._conn.cursor(row_factory=dict_row) as cur,
            ):
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM knowledge_chunks WHERE corpus_version = %s "
                    "AND embedder_id = %s)",
                    (self._corpus_version, self._embedder.embedder_id),
                )
                row = cur.fetchone()
                if not row or not row["exists"]:
                    raise KnowledgeUnavailableError(
                        f"no index for corpus {self._corpus_version} with embedder "
                        f"{self._embedder.embedder_id}; run scripts/index_knowledge.py"
                    )
                cur.execute(sql, params)
                return list(cur.fetchall())
        except psycopg.Error as exc:
            raise KnowledgeUnavailableError(
                f"knowledge store query failed: {type(exc).__name__}"
            ) from exc


def _embedding_text(chunk: KnowledgeChunk) -> str:
    """What gets embedded: the title anchors passages that never name their subject."""
    return f"{chunk.title}. {chunk.body}"


def _chunk(row: dict[str, Any]) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=row["chunk_id"],
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        source_url=row["source_url"],
        reviewed_on=row["reviewed_on"],
        attraction_id=row["attraction_id"],
    )
