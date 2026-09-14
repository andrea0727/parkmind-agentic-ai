"""
knowledge.* tools: search_policies, get_accessibility_requirements,
find_similar_attractions. Backed by services/clients/knowledge_store.py.
"""


def search_policies(query: str) -> dict:
    raise NotImplementedError


def get_accessibility_requirements(attraction_id: str) -> dict:
    raise NotImplementedError
