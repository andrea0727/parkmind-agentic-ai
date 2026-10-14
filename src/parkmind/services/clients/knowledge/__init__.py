"""Knowledge adapters behind ``parkmind.services.ports.KnowledgeStore`` (section 30 [C21]).

``in_memory`` serves fixtures, tests and the MVP; a pgvector adapter sits beside it.
The port does not change either way.
"""

from .in_memory import InMemoryKnowledgeStore
from .safety_notices import NOTICE_CORPUS_VERSION, magic_kingdom_knowledge_store

__all__ = [
    "NOTICE_CORPUS_VERSION",
    "InMemoryKnowledgeStore",
    "magic_kingdom_knowledge_store",
]
