"""Indexes the reviewed knowledge corpus into pgvector (backlog P0-26).

    poetry run python scripts/index_knowledge.py            # the configured model (fetch it first)
    poetry run python scripts/index_knowledge.py --hashing  # deterministic dev embedder, no model

Needs the schema (migration 0003: `alembic ... upgrade head`) on a Postgres with
pgvector (`docker compose up -d --build`). Idempotent: rows already indexed for
this corpus version and embedder are left alone, so re-running costs nothing.
Prints the corpus coverage over the curated attraction catalog (section 45).

Exit codes: 0 indexed, 1 database problem, 2 embedding model unavailable.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from parkmind.config.settings import settings
from parkmind.services.clients.knowledge.embeddings import (
    Embedder,
    FastEmbedEmbedder,
    HashingEmbedder,
)
from parkmind.services.clients.knowledge.knowledge_corpus import (
    corpus_coverage,
    knowledge_corpus,
)
from parkmind.services.clients.knowledge.pgvector_store import PgvectorKnowledgeSearch
from parkmind.services.clients.knowledge.safety_notices import (
    MAGIC_KINGDOM_SAFETY_NOTICES,
)
from parkmind.services.clients.postgres.connection import (
    DATABASE_PROBLEMS,
    connect,
    describe_database_problem,
)
from parkmind.services.ports import KnowledgeUnavailableError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--hashing",
        action="store_true",
        help="index with the deterministic dev embedder",
    )
    args = parser.parse_args(argv)

    embedder: Embedder = (
        HashingEmbedder()
        if args.hashing
        else FastEmbedEmbedder(
            settings.EMBEDDING_MODEL, cache_dir=settings.EMBEDDING_CACHE
        )
    )
    chunks = knowledge_corpus()
    try:
        with connect() as conn:
            report = PgvectorKnowledgeSearch(conn, embedder).index(chunks)
    except DATABASE_PROBLEMS as exc:
        print(describe_database_problem(exc), file=sys.stderr)
        return 1
    except KnowledgeUnavailableError as exc:
        print(exc, file=sys.stderr)
        return 2

    coverage = corpus_coverage(
        chunks, [n.attraction_id for n in MAGIC_KINGDOM_SAFETY_NOTICES]
    )
    print(
        f"corpus {report.corpus_version} with {report.embedder_id}: "
        f"{report.inserted} indexed now, {report.already_indexed} already there"
    )
    print(
        f"coverage: {coverage.passages} passages; profiles for "
        f"{len(coverage.with_profile)}/{len(coverage.with_profile) + len(coverage.without_profile)} "
        f"catalog attractions; accessibility passages for "
        f"{len(coverage.with_accessibility_passage)}"
    )
    if coverage.without_profile:
        print(f"no profile: {sorted(coverage.without_profile)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
