"""
In-memory knowledge store — a dict/JSON file loaded at startup. Replaces
pgvector for the month-1 build (see docs/decisions/scope.md). Same
interface either way, so swapping in a real vector DB later is a one-file
change.
"""


class InMemoryKnowledgeStore:
    def search_policies(self, query: str) -> list[dict]:
        raise NotImplementedError

    def get_accessibility_requirements(self, attraction_id: str) -> dict:
        raise NotImplementedError
