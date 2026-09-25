"""Provider ids -> stable internal ids (backlog P0-10).

**The internal id of an entity is the ThemeParks entity UUID it had when it was
first seen.** ThemeParks is the *anchor* provider: an unseen ThemeParks id mints
``internal_id = provider_id`` and records it. From then on every lookup goes
through ``id_mapping``, so stability comes from that table, not from the id
format -- if the provider ever re-issues an id, a curated mapping points the new
id at the old internal id. Ids from any other provider (Queue-Times, later) are
never auto-minted: until someone curates their mapping they are reported as
``UNMAPPED``.

Nothing is merged or guessed. A provider id already mapped to a different
internal id (``CONFLICT``), or two provider ids in one payload that land on the
same internal id (``DUPLICATE_INTERNAL_ID``), exclude the entities involved and
come back as ``MappingIssue``s next to the rest of the payload, which still
resolves.

Tracing back: ``trace(internal_id)`` lists every provider id recorded for it; a
plan's ``Provenance.snapshot_id`` leads to the snapshot's raw provider payload
(``SnapshotRepository.get_raw_payload``), where those provider ids were seen.
"""

from collections import defaultdict
from dataclasses import replace
from datetime import datetime

from parkmind.core.contracts import Attraction, DataSource
from parkmind.services.clients.normalization import (
    IssueKind,
    LiveEntity,
    MappingIssue,
    NormalizedCatalog,
    NormalizedLive,
)
from parkmind.services.ports import (
    EntityKind,
    IdMappingConflictError,
    IdMappingRepository,
)

ANCHOR_PROVIDER = DataSource.THEMEPARKS_WIKI


class IdResolver:
    def __init__(
        self, repo: IdMappingRepository, *, anchor: DataSource = ANCHOR_PROVIDER
    ) -> None:
        self._repo = repo
        self._anchor = anchor

    def resolve(
        self,
        provider: DataSource,
        provider_id: str,
        kind: EntityKind,
        *,
        seen_at: datetime,
    ) -> str | MappingIssue:
        """The internal id for one provider id, recording the sighting; or the issue."""
        _require_aware(seen_at)
        internal_id = self._repo.resolve(provider.value, provider_id, kind)
        if internal_id is None:
            if provider is not self._anchor:
                return MappingIssue(
                    kind=IssueKind.UNMAPPED,
                    provider=provider,
                    provider_id=provider_id,
                    entity_kind=kind,
                    detail=f"no curated mapping from {provider.value} to an internal id",
                )
            internal_id = provider_id
        try:
            self._repo.record(provider.value, provider_id, kind, internal_id, seen_at=seen_at)
        except IdMappingConflictError:
            # A concurrent writer mapped this provider id elsewhere between the
            # resolve and the record.
            return MappingIssue(
                kind=IssueKind.CONFLICT,
                provider=provider,
                provider_id=provider_id,
                entity_kind=kind,
                detail=f"already mapped to an internal id other than {internal_id!r}",
            )
        return internal_id

    def resolve_catalog(
        self, provider: DataSource, catalog: NormalizedCatalog, *, seen_at: datetime
    ) -> NormalizedCatalog:
        """The catalog with every ``node_id`` replaced by its internal id."""
        issues = list(catalog.issues)
        resolved: list[tuple[str, str, Attraction]] = []
        for attraction in catalog.attractions:
            kind = catalog.kinds[attraction.node_id]
            outcome = self.resolve(provider, attraction.node_id, kind, seen_at=seen_at)
            if isinstance(outcome, MappingIssue):
                issues.append(outcome)
            else:
                resolved.append((attraction.node_id, outcome, attraction))

        keep, collisions = _unique_internal_ids(
            provider, [(pid, iid, catalog.kinds[pid]) for pid, iid, _ in resolved]
        )
        issues.extend(collisions)
        return NormalizedCatalog(
            attractions=[
                attraction.model_copy(update={"node_id": internal_id})
                for provider_id, internal_id, attraction in resolved
                if provider_id in keep
            ],
            kinds={
                internal_id: catalog.kinds[provider_id]
                for provider_id, internal_id, _ in resolved
                if provider_id in keep
            },
            issues=issues,
        )

    def resolve_live(
        self, provider: DataSource, live: NormalizedLive, *, seen_at: datetime
    ) -> NormalizedLive:
        """Live readings re-keyed by internal id."""
        issues = list(live.issues)
        resolved: list[tuple[str, str, LiveEntity]] = []
        for provider_id, entity in live.entities.items():
            outcome = self.resolve(provider, provider_id, entity.kind, seen_at=seen_at)
            if isinstance(outcome, MappingIssue):
                issues.append(outcome)
            else:
                resolved.append((provider_id, outcome, entity))

        keep, collisions = _unique_internal_ids(
            provider, [(pid, iid, entity.kind) for pid, iid, entity in resolved]
        )
        issues.extend(collisions)
        return NormalizedLive(
            entities={
                internal_id: replace(entity, entity_id=internal_id)
                for provider_id, internal_id, entity in resolved
                if provider_id in keep
            },
            issues=issues,
        )

    def trace(self, internal_id: str) -> list[tuple[str, str, str]]:
        """Every ``(provider, provider_id, entity_kind)`` recorded for ``internal_id``."""
        return self._repo.provider_ids_for(internal_id)


def _unique_internal_ids(
    provider: DataSource, resolved: list[tuple[str, str, EntityKind]]
) -> tuple[set[str], list[MappingIssue]]:
    """Provider ids to keep, and an issue for every one that shares an internal id."""
    by_internal: dict[str, list[tuple[str, EntityKind]]] = defaultdict(list)
    for provider_id, internal_id, kind in resolved:
        by_internal[internal_id].append((provider_id, kind))

    keep: set[str] = set()
    issues: list[MappingIssue] = []
    for internal_id, sources in by_internal.items():
        if len(sources) == 1:
            keep.add(sources[0][0])
            continue
        issues.extend(
            MappingIssue(
                kind=IssueKind.DUPLICATE_INTERNAL_ID,
                provider=provider,
                provider_id=provider_id,
                entity_kind=kind,
                detail=f"{len(sources)} provider ids resolve to internal id {internal_id!r}",
            )
            for provider_id, kind in sources
        )
    return keep, issues


def _require_aware(seen_at: datetime) -> None:
    if seen_at.tzinfo is None or seen_at.utcoffset() is None:
        raise ValueError("seen_at must be timezone-aware")
