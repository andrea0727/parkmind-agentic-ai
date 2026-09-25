"""Provider-neutral results of normalizing raw provider payloads (backlog P0-10).

A normalizer turns one raw payload into ParkMind contracts plus a list of
``MappingIssue``s. An entity with an identity problem -- a duplicated provider
id, missing curated metadata, an unknown entity type, an unmapped or
conflicting id -- is **excluded and reported**, never merged or guessed, so the
rest of the payload still normalizes and the caller decides what the gap means.
Structural drift (unknown status, malformed field, foreign timezone) is not an
issue: it raises the adapter's schema error, as it always did.

These are services-level result types, not section 33 contracts: they never
enter the core.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from parkmind.core.contracts import Attraction, AttractionStatus, DataSource
from parkmind.services.ports.id_mapping_repository import EntityKind


class IssueKind(StrEnum):
    DUPLICATE_PROVIDER_ID = "DUPLICATE_PROVIDER_ID"
    """The same provider id appears more than once in one payload; every copy is excluded."""
    DUPLICATE_INTERNAL_ID = "DUPLICATE_INTERNAL_ID"
    """Two provider ids in one payload resolve to the same internal id; both are excluded."""
    MISSING_METADATA = "MISSING_METADATA"
    """A catalog entity has no curated metadata, so it cannot become an ``Attraction``."""
    UNKNOWN_ENTITY_KIND = "UNKNOWN_ENTITY_KIND"
    """The provider reports an entity type outside ``EntityKind``."""
    UNMAPPED = "UNMAPPED"
    """A non-anchor provider id has no mapping yet; it is never auto-minted."""
    CONFLICT = "CONFLICT"
    """The provider id is already mapped to a different internal id."""


@dataclass(frozen=True)
class MappingIssue:
    kind: IssueKind
    provider: DataSource
    provider_id: str
    entity_kind: EntityKind | None
    detail: str


@dataclass(frozen=True)
class LiveEntity:
    """One live reading with canonical values: enum status, park-local aware times."""

    entity_id: str
    kind: EntityKind
    status: AttractionStatus
    standby_wait_minutes: float | None
    showtimes: tuple[datetime, ...]
    observed_at: datetime | None
    """The provider's own ``lastUpdated`` for this entity, not the fetch time."""


@dataclass(frozen=True)
class NormalizedCatalog:
    attractions: list[Attraction]
    kinds: dict[str, EntityKind] = field(default_factory=dict)
    """Entity kind per ``Attraction.node_id`` (an ``Attraction`` doesn't carry it)."""
    issues: list[MappingIssue] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedLive:
    """Live readings keyed by entity id (a provider id before resolution, an
    internal id after ``IdResolver``)."""

    entities: dict[str, LiveEntity]
    issues: list[MappingIssue] = field(default_factory=list)
