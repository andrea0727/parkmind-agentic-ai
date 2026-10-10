"""Knowledge store on pgvector (P0-26).

Revision ID: 0003
Revises: 0002

The semantic half of the ``KnowledgeStore`` (Architecture section 30 [C21]):
``search_policies`` and ``find_similar_attractions`` rank chunks of the reviewed
knowledge corpus by cosine distance. The safety notices behind
``check_accessibility`` are not stored here -- they stay in the curated
in-memory corpus, so rule 10 never depends on retrieval (section 43).

One row = one chunk of one corpus version, embedded by one embedder.
``embedder_id`` is part of the key: vectors from different models are never
compared, and re-indexing with a new model leaves the old rows untouched.
384 is the dimension of the configured multilingual MiniLM model.
"""

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

EMBEDDING_DIMENSION = 384


def _require_pgvector() -> None:
    """Stop with an actionable message instead of 'extension "vector" is not available'."""
    available = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"))
        .scalar()
    )
    if not available:
        raise CommandError(
            "This PostgreSQL server has no pgvector extension. Rebuild the dev "
            "database image (the data volume is kept) and migrate again:\n"
            "  docker compose up -d --build\n"
            "  poetry run alembic -c database/alembic.ini upgrade head"
        )


def upgrade() -> None:
    _require_pgvector()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"""
        CREATE TABLE knowledge_chunks (
            corpus_version TEXT NOT NULL,
            embedder_id    TEXT NOT NULL,
            chunk_id       TEXT NOT NULL,
            kind           TEXT NOT NULL
                CHECK (kind IN ('policy', 'faq', 'accessibility', 'attraction_profile')),
            attraction_id  TEXT,
            title          TEXT NOT NULL,
            body           TEXT NOT NULL,
            source_url     TEXT NOT NULL,
            reviewed_on    DATE NOT NULL,
            embedding      vector({EMBEDDING_DIMENSION}) NOT NULL,
            indexed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (corpus_version, embedder_id, chunk_id),
            CHECK (kind <> 'attraction_profile' OR attraction_id IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX knowledge_chunks_embedding_idx "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
    op.execute("DROP EXTENSION IF EXISTS vector")
