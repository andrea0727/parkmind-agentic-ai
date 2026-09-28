"""
knowledge.* tools: search_policies, get_accessibility_requirements,
find_similar_attractions. Backed by the KnowledgeStore port
(services/clients/knowledge/); check_accessibility lives in
services/use_cases/check_accessibility.py (P0-26a). Exposed as MCP tools in P0-26.
"""


def search_policies(query: str) -> dict:
    raise NotImplementedError


def get_accessibility_requirements(attraction_id: str) -> dict:
    raise NotImplementedError
