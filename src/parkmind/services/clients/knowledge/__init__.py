"""Knowledge adapters behind ``parkmind.services.ports.KnowledgeStore`` (section 30 [C21]).

``in_memory`` serves fixtures, tests and the MVP; a pgvector adapter joins it
in P0-26. Which one the demo runs is the P0-01 ADR decision -- the port does
not change either way.
"""

from .in_memory import InMemoryKnowledgeStore
from .safety_notices import NOTICE_CORPUS_VERSION, magic_kingdom_knowledge_store

__all__ = [
    "NOTICE_CORPUS_VERSION",
    "InMemoryKnowledgeStore",
    "magic_kingdom_knowledge_store",
]
