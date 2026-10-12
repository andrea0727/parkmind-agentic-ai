"""The reviewed knowledge corpus behind semantic search (P0-26; Architecture section 30).

Passages quoted from the official Walt Disney World pages -- park policies
(Rider Switch, service animals, strollers, wheelchairs, first aid, baby care,
lost and found), accessibility information, and one profile per catalog
attraction -- stored in ``corpus/knowledge_corpus_<version>.json``. The pages
are served in Spanish (es-co) whatever locale is requested, so the passages are
Spanish; the multilingual embedding model matches English questions to them.

Bodies are verbatim and short (at most ``MAX_WORDS`` words: the model reads 128
tokens); each keeps its ``source_url`` and review date. Titles are page titles,
or the catalog name for passages about one attraction. The parks FAQ page is
rendered in the browser and has no text to quote, so this version has no
``faq`` passages.

Like the safety-notice corpus (``safety_notices.py``), this is a reviewed
artifact: regenerate it with the curation scripts, review the diff, and bump
``KNOWLEDGE_CORPUS_VERSION`` -- the version goes into every search's provenance
and keys the pgvector index.
"""

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import get_args

from parkmind.services.ports import KnowledgeChunk, KnowledgeKind

KNOWLEDGE_CORPUS_VERSION = "2026-10-10"
MAX_WORDS = 90
OFFICIAL_SITE = "https://disneyworld.disney.go.com/"

_FILE = (
    Path(__file__).parent
    / "corpus"
    / f"knowledge_corpus_{KNOWLEDGE_CORPUS_VERSION}.json"
)
_KINDS = frozenset(get_args(KnowledgeKind))


class CorpusFileError(ValueError):
    """The corpus file does not hold a valid reviewed corpus."""


@lru_cache(maxsize=1)
def knowledge_corpus() -> tuple[KnowledgeChunk, ...]:
    """Every passage of the current corpus version, validated."""
    document = json.loads(_FILE.read_text(encoding="utf-8"))
    if document.get("version") != KNOWLEDGE_CORPUS_VERSION:
        raise CorpusFileError(f"{_FILE.name} is version {document.get('version')!r}")
    reviewed_on = date.fromisoformat(document["reviewed_on"])
    chunks = tuple(_chunk(raw, reviewed_on) for raw in document["chunks"])
    ids = [c.chunk_id for c in chunks]
    if len(set(ids)) != len(ids):
        raise CorpusFileError("chunk ids must be unique")
    return chunks


def _chunk(raw: dict[str, object], reviewed_on: date) -> KnowledgeChunk:
    chunk = KnowledgeChunk(
        chunk_id=str(raw["chunk_id"]),
        kind=raw["kind"],  # type: ignore[arg-type]  # checked below
        title=str(raw["title"]),
        body=str(raw["body"]),
        source_url=str(raw["source_url"]),
        reviewed_on=reviewed_on,
        attraction_id=raw.get("attraction_id") or None,  # type: ignore[arg-type]
    )
    problems = []
    if chunk.kind not in _KINDS:
        problems.append(f"unknown kind {chunk.kind!r}")
    if chunk.kind == "attraction_profile" and not chunk.attraction_id:
        problems.append("a profile needs its attraction_id")
    if not chunk.title.strip() or not chunk.body.strip():
        problems.append("empty title or body")
    if len(chunk.body.split()) > MAX_WORDS:
        problems.append(f"body longer than {MAX_WORDS} words")
    if not chunk.source_url.startswith(OFFICIAL_SITE):
        problems.append("source is not the official park site")
    if "�" in chunk.body:
        problems.append("body holds a replacement character (encoding damage)")
    if problems:
        raise CorpusFileError(f"{chunk.chunk_id}: {'; '.join(problems)}")
    return chunk


@dataclass(frozen=True)
class CorpusCoverage:
    """Which catalog attractions the corpus covers (section 45: coverage is a product metric)."""

    corpus_version: str
    passages: int
    with_profile: frozenset[str]
    without_profile: frozenset[str]
    with_accessibility_passage: frozenset[str]
    unknown_attraction_ids: frozenset[str]
    """Attraction ids in the corpus that the catalog does not have."""


def corpus_coverage(
    chunks: Sequence[KnowledgeChunk], catalog_ids: Collection[str]
) -> CorpusCoverage:
    catalog = frozenset(catalog_ids)
    profiled = frozenset(
        c.attraction_id
        for c in chunks
        if c.kind == "attraction_profile" and c.attraction_id
    )
    accessible = frozenset(
        c.attraction_id for c in chunks if c.kind == "accessibility" and c.attraction_id
    )
    mentioned = frozenset(c.attraction_id for c in chunks if c.attraction_id)
    return CorpusCoverage(
        corpus_version=KNOWLEDGE_CORPUS_VERSION,
        passages=len(chunks),
        with_profile=profiled & catalog,
        without_profile=catalog - profiled,
        with_accessibility_passage=accessible & catalog,
        unknown_attraction_ids=mentioned - catalog,
    )
