"""KnowledgeStore port and its in-memory adapter (P0-26a)."""

import pytest

from parkmind.core.contracts import RideRestriction
from parkmind.services import ports
from parkmind.services.clients.knowledge import InMemoryKnowledgeStore
from parkmind.services.ports import KnowledgeStore

HIGH_G = RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE
TRANSFER = RideRestriction.REQUIRES_TRANSFER_FROM_WHEELCHAIR


def _accepts_port(store: KnowledgeStore) -> KnowledgeStore:
    return store


def test_knowledge_store_is_exported_from_ports() -> None:
    assert "KnowledgeStore" in ports.__all__
    assert ports.KnowledgeStore.__module__ == "parkmind.services.ports.knowledge_store"


def test_in_memory_store_satisfies_the_port() -> None:
    store = _accepts_port(InMemoryKnowledgeStore({"a1": [HIGH_G]}, corpus_version="v1"))

    assert store.corpus_version == "v1"


def test_notice_for_returns_the_published_flags() -> None:
    store = InMemoryKnowledgeStore({"a1": [HIGH_G, TRANSFER]}, corpus_version="v1")

    assert store.notice_for("a1") == frozenset({HIGH_G, TRANSFER})


def test_an_empty_notice_is_on_file_but_restricts_nothing() -> None:
    store = InMemoryKnowledgeStore({"a1": []}, corpus_version="v1")

    assert store.notice_for("a1") == frozenset()
    assert "a1" in store.covered_attraction_ids()


def test_an_attraction_without_a_notice_returns_none() -> None:
    store = InMemoryKnowledgeStore({"a1": [HIGH_G]}, corpus_version="v1")

    assert store.notice_for("unknown") is None


def test_covered_attraction_ids_lists_every_notice() -> None:
    store = InMemoryKnowledgeStore({"a1": [HIGH_G], "a2": []}, corpus_version="v1")

    assert store.covered_attraction_ids() == frozenset({"a1", "a2"})


def test_adapter_rejects_free_text_flags() -> None:
    with pytest.raises(TypeError, match="not RideRestriction members"):
        InMemoryKnowledgeStore(
            {"a1": ["NOT_RECOMMENDED_HIGH_G_FORCE"]},  # type: ignore[list-item]
            corpus_version="v1",
        )


def test_adapter_requires_a_corpus_version() -> None:
    with pytest.raises(ValueError, match="version"):
        InMemoryKnowledgeStore({"a1": [HIGH_G]}, corpus_version="")
