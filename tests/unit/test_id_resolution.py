"""P0-10: stable internal ids, and mapping problems surfaced instead of merged.

``IdMappingRepository`` is the only thing faked (a port): the fake keeps the
Postgres adapter's documented semantics -- a first sighting inserts, the same
mapping again only moves ``last_seen_at``, a re-map to a different internal id
raises ``IdMappingConflictError``. tests/integration/postgres/ runs the same
resolver against the real adapter.
"""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from parkmind.core.contracts import DataSource
from parkmind.core.contracts.base import PARK_TZ
from parkmind.services.clients.normalization import IssueKind
from parkmind.services.clients.themeparks_normalize import parse_catalog, parse_live
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
)
from parkmind.services.ports import EntityKind, IdMappingConflictError
from parkmind.services.use_cases.id_resolution import IdResolver

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "themeparks"
THEMEPARKS = DataSource.THEMEPARKS_WIKI
SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"
T0 = datetime(2026, 9, 16, 9, 0, tzinfo=PARK_TZ)


class InMemoryIdMappingRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict[str, object]] = {}

    def record(
        self,
        provider: str,
        provider_id: str,
        entity_kind: str,
        internal_id: str,
        *,
        seen_at: datetime,
    ) -> None:
        key = (provider, provider_id, str(entity_kind))
        row = self.rows.get(key)
        if row is None:
            self.rows[key] = {"internal_id": internal_id, "first": seen_at, "last": seen_at}
            return
        if row["internal_id"] != internal_id:
            raise IdMappingConflictError("provider id already mapped elsewhere")
        row["last"] = max(row["last"], seen_at)  # type: ignore[type-var]

    def resolve(self, provider: str, provider_id: str, entity_kind: str) -> str | None:
        row = self.rows.get((provider, provider_id, str(entity_kind)))
        return None if row is None else str(row["internal_id"])

    def provider_ids_for(self, internal_id: str) -> list[tuple[str, str, str]]:
        return sorted(k for k, row in self.rows.items() if row["internal_id"] == internal_id)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _resolver() -> tuple[IdResolver, InMemoryIdMappingRepository]:
    repo = InMemoryIdMappingRepository()
    return IdResolver(repo), repo


# ------------------------------------------------------------------- stability


def test_same_attraction_keeps_its_internal_id_across_snapshots() -> None:
    resolver, _ = _resolver()
    first = _fixture("live_magic_kingdom.json")
    later = copy.deepcopy(first)
    for entity in later["liveData"]:
        entity["lastUpdated"] = "2026-09-16T15:10:00Z"
        if entity["id"] == SPACE_MOUNTAIN:
            entity["queue"]["STANDBY"]["waitTime"] = 85

    one = resolver.resolve_live(THEMEPARKS, parse_live(first), seen_at=T0)
    two = resolver.resolve_live(THEMEPARKS, parse_live(later), seen_at=T0 + timedelta(hours=2))

    assert set(one.entities) == set(two.entities)
    assert two.entities[SPACE_MOUNTAIN].standby_wait_minutes == 85
    assert one.issues == two.issues == []


def test_themeparks_is_the_anchor_its_uuid_becomes_the_internal_id() -> None:
    resolver, repo = _resolver()

    catalog = resolver.resolve_catalog(
        THEMEPARKS,
        parse_catalog(_fixture("children_magic_kingdom.json"), MAGIC_KINGDOM_ATTRACTION_METADATA),
        seen_at=T0,
    )

    assert SPACE_MOUNTAIN in {a.node_id for a in catalog.attractions}
    assert repo.resolve(THEMEPARKS.value, SPACE_MOUNTAIN, EntityKind.ATTRACTION) == SPACE_MOUNTAIN
    assert catalog.kinds[SPACE_MOUNTAIN] is EntityKind.ATTRACTION


def test_a_curated_remap_keeps_the_old_internal_id_when_the_provider_id_changes() -> None:
    """Stability comes from the mapping table, not the id format."""
    resolver, repo = _resolver()
    reissued = "11111111-2222-3333-4444-555555555555"
    repo.record(THEMEPARKS.value, reissued, EntityKind.ATTRACTION, SPACE_MOUNTAIN, seen_at=T0)
    payload = _fixture("live_magic_kingdom.json")
    for entity in payload["liveData"]:
        if entity["id"] == SPACE_MOUNTAIN:
            entity["id"] = reissued

    live = resolver.resolve_live(THEMEPARKS, parse_live(payload), seen_at=T0)

    assert SPACE_MOUNTAIN in live.entities
    assert reissued not in live.entities
    assert live.entities[SPACE_MOUNTAIN].entity_id == SPACE_MOUNTAIN


def test_trace_lists_every_provider_id_of_an_internal_id() -> None:
    resolver, repo = _resolver()
    resolver.resolve_live(THEMEPARKS, parse_live(_fixture("live_magic_kingdom.json")), seen_at=T0)
    repo.record(DataSource.QUEUE_TIMES.value, "qt-284", EntityKind.ATTRACTION, SPACE_MOUNTAIN, seen_at=T0)

    assert resolver.trace(SPACE_MOUNTAIN) == [
        ("queue_times", "qt-284", "attraction"),
        ("themeparks_wiki", SPACE_MOUNTAIN, "attraction"),
    ]


# ---------------------------------------------- surfaced, never silently merged


def test_conflicting_remap_is_reported_and_excluded_rest_resolves() -> None:
    class RacingRepo(InMemoryIdMappingRepository):
        """Another writer maps Space Mountain elsewhere between resolve and record."""

        def record(self, provider, provider_id, entity_kind, internal_id, *, seen_at):  # type: ignore[no-untyped-def]
            if provider_id == SPACE_MOUNTAIN and (provider, provider_id, str(entity_kind)) not in self.rows:
                self.rows[(provider, provider_id, str(entity_kind))] = {
                    "internal_id": "someone-else",
                    "first": seen_at,
                    "last": seen_at,
                }
            super().record(provider, provider_id, entity_kind, internal_id, seen_at=seen_at)

    resolver = IdResolver(RacingRepo())

    live = resolver.resolve_live(THEMEPARKS, parse_live(_fixture("live_magic_kingdom.json")), seen_at=T0)

    assert SPACE_MOUNTAIN not in live.entities
    assert [(i.kind, i.provider_id) for i in live.issues] == [(IssueKind.CONFLICT, SPACE_MOUNTAIN)]
    assert BIG_THUNDER in live.entities


def test_unmapped_non_anchor_provider_id_is_reported_not_minted() -> None:
    resolver, repo = _resolver()
    live = parse_live(_fixture("live_magic_kingdom.json"))

    resolved = resolver.resolve_live(DataSource.QUEUE_TIMES, live, seen_at=T0)

    assert resolved.entities == {}
    assert {i.kind for i in resolved.issues} == {IssueKind.UNMAPPED}
    assert len(resolved.issues) == len(live.entities)
    assert repo.rows == {}  # nothing was minted for a non-anchor provider


def test_two_provider_ids_on_one_internal_id_are_both_excluded() -> None:
    resolver, repo = _resolver()
    # A bad curated mapping points Big Thunder's id at Space Mountain.
    repo.record(THEMEPARKS.value, BIG_THUNDER, EntityKind.ATTRACTION, SPACE_MOUNTAIN, seen_at=T0)

    live = resolver.resolve_live(THEMEPARKS, parse_live(_fixture("live_magic_kingdom.json")), seen_at=T0)

    assert SPACE_MOUNTAIN not in live.entities
    assert {(i.kind, i.provider_id) for i in live.issues} == {
        (IssueKind.DUPLICATE_INTERNAL_ID, SPACE_MOUNTAIN),
        (IssueKind.DUPLICATE_INTERNAL_ID, BIG_THUNDER),
    }


def test_normalizer_issues_are_carried_through_resolution() -> None:
    resolver, _ = _resolver()

    live = resolver.resolve_live(
        THEMEPARKS, parse_live(_fixture("live_magic_kingdom_duplicate_id.json")), seen_at=T0
    )

    assert (IssueKind.DUPLICATE_PROVIDER_ID, SPACE_MOUNTAIN) in {
        (i.kind, i.provider_id) for i in live.issues
    }


def test_seen_at_must_be_timezone_aware() -> None:
    resolver, _ = _resolver()

    with pytest.raises(ValueError, match="timezone-aware"):
        resolver.resolve(THEMEPARKS, SPACE_MOUNTAIN, EntityKind.ATTRACTION, seen_at=datetime(2026, 9, 16))  # noqa: DTZ001 -- naive on purpose


def test_catalog_kinds_are_required() -> None:
    """A catalog built without kinds can't reach resolve_catalog and crash there."""
    from parkmind.services.clients.normalization import NormalizedCatalog

    with pytest.raises(TypeError):
        NormalizedCatalog(attractions=[])  # type: ignore[call-arg]
