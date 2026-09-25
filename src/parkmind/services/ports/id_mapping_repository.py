"""IdMappingRepository -- provider id -> stable internal id (backlog P0-10).

Storage only, typed with plain values: section 33 defines no ``IdMapping``
contract, and adding one is a baseline change. ``EntityKind`` is the closed
``entity_kind`` vocabulary (P0-10); it is a ``StrEnum``, so it can be passed
wherever the port takes a ``str``. The model around this port lives in
``services.use_cases.id_resolution``.
"""

from datetime import datetime
from enum import StrEnum
from typing import Protocol


class EntityKind(StrEnum):
    """What a mapped provider entity is. Distinct kinds are distinct namespaces."""

    ATTRACTION = "attraction"
    SHOW = "show"
    RESTAURANT = "restaurant"
    PARK = "park"


class IdMappingRepository(Protocol):
    def record(
        self,
        provider: str,
        provider_id: str,
        entity_kind: str,
        internal_id: str,
        *,
        seen_at: datetime,
    ) -> None:
        """Record a sighting of ``provider_id``.

        A first sighting inserts the mapping; the same mapping again only moves
        ``last_seen_at`` forward. Mapping an already-mapped provider id to a
        *different* ``internal_id`` raises ``IdMappingConflictError`` -- it is
        never overwritten or merged silently.
        """
        ...

    def resolve(self, provider: str, provider_id: str, entity_kind: str) -> str | None: ...

    def provider_ids_for(self, internal_id: str) -> list[tuple[str, str, str]]:
        """``(provider, provider_id, entity_kind)`` triples for an internal id."""
        ...
