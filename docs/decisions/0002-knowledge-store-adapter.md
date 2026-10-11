# ADR 0002 — The demo's knowledge store runs on pgvector

- **Status:** Proposed (2026-10-10)
- **Backlog:** P0-26 (#40); the decision P0-01 delegates to an ADR
- **Architecture:** §30 [C21], §41, §43, §45

## Context

Architecture §30 [C21] puts the knowledge store behind a `KnowledgeStore` port with
two adapters, pgvector (the baseline) and in-memory (fixtures and tests), and
leaves to an ADR which one the demo runs (backlog P0-01). P0-26 asks for semantic
retrieval over a versioned corpus (`search_policies`, `find_similar_attractions`),
a pgvector adapter, and Compose and CI running a pgvector-enabled Postgres.

The capability has two parts with different safety needs:

- **Accessibility matching** (`check_accessibility`, rule 10) must never degrade
  (§43): it fails closed when no notice is on file (P0-26a).
- **Semantic search** may degrade (§43: "retrieval → static rules / known metadata").

## Decision

1. **The demo runs semantic search on pgvector** (migration `0003`,
   `knowledge_chunks`, HNSW cosine index), behind a `KnowledgeSearch` port that
   sits next to `KnowledgeStore` in `services/ports/knowledge_store.py`.
2. **Embeddings are computed locally** with fastembed and
   `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions,
   ~220 MB, Apache-2.0). The official park pages are served in Spanish whatever
   locale is requested, so the model must be multilingual: English questions are
   matched against Spanish passages. The weights are downloaded once into a
   persistent cache (`scripts/fetch_embedding_model.py`) and only ever loaded from
   it (`local_files_only`): no request triggers a download.
3. **Accessibility notices stay in the curated in-memory corpus** for every
   configuration: rule 10 never depends on retrieval.
4. **The in-memory keyword search is the fallback**, not a demo mode: BM25 over the
   same passages, used by tests and whenever semantic search cannot answer (no
   model, no index for this corpus and model, database failure). Every answer says
   which strategy produced it (`provenance.strategy`, `provenance.degraded`).
5. **Rows are keyed by `(corpus_version, embedder_id, chunk_id)`**: vectors from
   different models are never compared; a model change re-indexes alongside.

`PARKMIND_KNOWLEDGE_BACKEND=in_memory` selects keyword-only search (no pgvector
needed); the default is `pgvector`.

## Consequences

- **Setup:** `docker compose up -d --build` (the image is `postgres:16` from ECR
  Public plus `postgresql-16-pgvector` from the PGDG apt source), migrate, fetch the
  model once, `scripts/index_knowledge.py`.
- **CI:** the PR job installs the same apt package into its Postgres service
  container and never downloads the model (tests use a deterministic hashing
  embedder). The manual **Embedding smoke** workflow runs the `requires_model`
  tests with the real model, cached between runs.
- **Isolation:** each retrieval query runs in its own savepoint, so a failure
  cannot undo guest state written on the same connection.
- **Privacy:** `knowledge.check_accessibility` takes derived flags, as §30
  defines its input, never `AccessibilityRequirements` (C19 keeps those out of
  state, checkpoints and traces). An external client such as the LLM-only
  baseline (§44) can then check eligibility without a session. LOAD CONTEXT over
  MCP sends the flags without the guest's id, and its transport errors never
  quote a request.
- **Measured** on the 2026-09-27 capture: ~57 ms per query, 13 s to index the
  134-passage corpus.

## Alternatives considered

- **In-memory only** — no semantic search; P0-26's pgvector Done-when unmet.
- **Voyage AI embeddings** — best quality, but an API key, network access and cost
  on the demo path.
- **Hashing embedder in production** — reproducible and dependency-free, but
  lexical: "similar to Pirates" would match only shared words.

## Open questions (not decided here)

- The parks FAQ page renders in the browser and has no quotable text: this
  corpus version has no FAQ passages.
- Intensity tiers for "less intense" come from the safety notices, which mark only
  five attractions with the full rider warning; grading intensity is a product
  decision (P0-26a).
- A paraphrase of Rider Switch ("wait with my baby while my partner rides") does
  not reach those passages with this model.
