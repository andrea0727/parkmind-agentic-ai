"""The reviewed knowledge corpus (P0-26 Done-when: "A small seed corpus is
indexed; the corpus has a version and a coverage report over the attraction
catalog")."""

import json

import pytest
from mcp_support import mk_catalog

from parkmind.services.clients.knowledge import knowledge_corpus as module
from parkmind.services.clients.knowledge.knowledge_corpus import (
    KNOWLEDGE_CORPUS_VERSION,
    MAX_WORDS,
    CorpusFileError,
    corpus_coverage,
    knowledge_corpus,
)


def test_corpus_versioned_with_catalog_coverage() -> None:
    chunks = knowledge_corpus()
    catalog_ids = [a.node_id for a in mk_catalog()]

    coverage = corpus_coverage(chunks, catalog_ids)

    assert coverage.corpus_version == KNOWLEDGE_CORPUS_VERSION
    assert coverage.passages == len(chunks) > 100
    assert coverage.without_profile == frozenset(), (
        "every catalog attraction has a profile"
    )
    assert coverage.unknown_attraction_ids == frozenset()
    assert len(coverage.with_accessibility_passage) > 0


def test_every_passage_is_a_short_quote_from_the_official_site() -> None:
    for chunk in knowledge_corpus():
        assert len(chunk.body.split()) <= MAX_WORDS, chunk.chunk_id
        assert chunk.source_url.startswith("https://disneyworld.disney.go.com/"), (
            chunk.chunk_id
        )
        assert "�" not in chunk.body, chunk.chunk_id


def test_the_corpus_holds_policies_accessibility_and_profiles() -> None:
    kinds = {chunk.kind for chunk in knowledge_corpus()}

    assert {"policy", "accessibility", "attraction_profile"} <= kinds


def test_a_profile_without_an_attraction_is_rejected(tmp_path, monkeypatch) -> None:
    document = {
        "version": KNOWLEDGE_CORPUS_VERSION,
        "reviewed_on": "2026-10-10",
        "chunks": [
            {
                "chunk_id": "attraction_profile/x",
                "kind": "attraction_profile",
                "attraction_id": None,
                "title": "X",
                "body": "Un paseo.",
                "source_url": "https://disneyworld.disney.go.com/x/",
            }
        ],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(module, "_FILE", path)
    knowledge_corpus.cache_clear()
    try:
        with pytest.raises(CorpusFileError, match="attraction_id"):
            knowledge_corpus()
    finally:
        knowledge_corpus.cache_clear()


def test_a_file_of_another_version_is_rejected(tmp_path, monkeypatch) -> None:
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps({"version": "1999-01-01", "reviewed_on": "1999-01-01", "chunks": []})
    )
    monkeypatch.setattr(module, "_FILE", path)
    knowledge_corpus.cache_clear()
    try:
        with pytest.raises(CorpusFileError, match="version"):
            knowledge_corpus()
    finally:
        knowledge_corpus.cache_clear()
