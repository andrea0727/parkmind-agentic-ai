"""KnowledgeStore port and its in-memory adapter (P0-26a)."""

from parkmind.services import ports


def test_knowledge_store_is_exported_from_ports() -> None:
    assert "KnowledgeStore" in ports.__all__
    assert ports.KnowledgeStore.__module__ == "parkmind.services.ports.knowledge_store"
