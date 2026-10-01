"""KnowledgeStore port and its in-memory adapter (P0-26a)."""

import pytest

from parkmind.core.contracts import RideRestriction
from parkmind.services import ports
from parkmind.services.clients.knowledge import (
    NOTICE_CORPUS_VERSION,
    InMemoryKnowledgeStore,
    magic_kingdom_knowledge_store,
)
from parkmind.services.clients.knowledge.safety_notices import (
    MAGIC_KINGDOM_SAFETY_NOTICES,
)
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
)
from parkmind.services.ports import KnowledgeStore
from parkmind.services.use_cases.check_accessibility import notice_coverage

HIGH_G = RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE
TRANSFER = RideRestriction.REQUIRES_TRANSFER_FROM_WHEELCHAIR


def _accepts_port(store: KnowledgeStore) -> KnowledgeStore:
    return store


def test_knowledge_store_is_exported_from_ports() -> None:
    assert "KnowledgeStore" in ports.__all__
    assert ports.KnowledgeStore.__module__ == "parkmind.services.ports.knowledge_store"


def test_in_memory_store_satisfies_the_port() -> None:
    store = _accepts_port(InMemoryKnowledgeStore({"a1": [HIGH_G]}, corpus_version="v1"))

    assert store.corpus_version == "v1"


def test_notice_for_returns_the_published_flags() -> None:
    store = InMemoryKnowledgeStore({"a1": [HIGH_G, TRANSFER]}, corpus_version="v1")

    assert store.notice_for("a1") == frozenset({HIGH_G, TRANSFER})


def test_an_empty_notice_is_on_file_but_restricts_nothing() -> None:
    store = InMemoryKnowledgeStore({"a1": []}, corpus_version="v1")

    assert store.notice_for("a1") == frozenset()
    assert "a1" in store.covered_attraction_ids()


def test_an_attraction_without_a_notice_returns_none() -> None:
    store = InMemoryKnowledgeStore({"a1": [HIGH_G]}, corpus_version="v1")

    assert store.notice_for("unknown") is None


def test_covered_attraction_ids_lists_every_notice() -> None:
    store = InMemoryKnowledgeStore({"a1": [HIGH_G], "a2": []}, corpus_version="v1")

    assert store.covered_attraction_ids() == frozenset({"a1", "a2"})


def test_adapter_rejects_free_text_flags() -> None:
    with pytest.raises(TypeError, match="not RideRestriction members"):
        InMemoryKnowledgeStore(
            {"a1": ["NOT_RECOMMENDED_HIGH_G_FORCE"]},  # type: ignore[list-item]
            corpus_version="v1",
        )


def test_adapter_requires_a_corpus_version() -> None:
    with pytest.raises(ValueError, match="version"):
        InMemoryKnowledgeStore({"a1": [HIGH_G]}, corpus_version="")


# --- the reviewed Magic Kingdom corpus (clients/knowledge/safety_notices.py) ---

_EVIDENCE_FLAGS = {
    "transfer-from-wheelchair": {TRANSFER},
    "transfer-to-wheelchair-then-ride": {TRANSFER},
    "ambulatory": {TRANSFER},
    "transfer-to-wheelchair": set(),
    "wheelchair-accessibility": set(),
    "no-service-animals": {RideRestriction.USES_SERVICE_ANIMAL},
    "no-service-animals-in-some-areas": {RideRestriction.USES_SERVICE_ANIMAL},
    "service-animals-with-caution": set(),
    "expectant-mothers": {RideRestriction.NOT_RECOMMENDED_EXPECTANT},
    "rider-warning": {
        HIGH_G,
        RideRestriction.NOT_RECOMMENDED_MOTION_SENSITIVITY,
        RideRestriction.NOT_RECOMMENDED_HEART_CONDITION,
        RideRestriction.NOT_RECOMMENDED_BACK_NECK,
        RideRestriction.NOT_RECOMMENDED_EXPECTANT,
    },
}
SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
SEVEN_DWARFS = "9d4d5229-7142-44b6-b4fb-528920969a2c"
SMALL_WORLD = "f5aad2d4-a419-4384-bd9a-42f86385c750"
HAPPILY_EVER_AFTER = "22b78ed9-a692-47cb-b6a4-6d1224ff67e3"


def test_corpus_has_a_version() -> None:
    assert NOTICE_CORPUS_VERSION
    assert magic_kingdom_knowledge_store().corpus_version == NOTICE_CORPUS_VERSION


def test_magic_kingdom_store_builds_offline() -> None:
    store = magic_kingdom_knowledge_store()

    assert len(store.covered_attraction_ids()) == len(MAGIC_KINGDOM_SAFETY_NOTICES)


def test_corpus_ids_are_unique() -> None:
    ids = [notice.attraction_id for notice in MAGIC_KINGDOM_SAFETY_NOTICES]

    assert len(ids) == len(set(ids))


def test_every_corpus_id_is_in_the_curated_catalog() -> None:
    corpus_ids = {notice.attraction_id for notice in MAGIC_KINGDOM_SAFETY_NOTICES}

    assert corpus_ids <= set(MAGIC_KINGDOM_ATTRACTION_METADATA)


def test_no_corpus_entry_is_orphaned_from_the_catalog() -> None:
    """The other direction of the check above: every entry is still a catalog id.

    A re-issued provider UUID would otherwise leave a notice nobody can look up.
    """
    coverage = notice_coverage(
        magic_kingdom_knowledge_store(), MAGIC_KINGDOM_ATTRACTION_METADATA
    )

    assert coverage.not_in_catalog == ()


def test_every_curated_catalog_entity_has_a_notice() -> None:
    """Rule 10 applies to ATTRACTION and SHOW stops, and an uncovered entity fails
    closed for every guest with restrictions: the corpus covers the whole curated
    catalog, attractions, shows and meet-and-greets alike (#69)."""
    coverage = notice_coverage(
        magic_kingdom_knowledge_store(), MAGIC_KINGDOM_ATTRACTION_METADATA
    )

    assert coverage.missing == ()
    assert len(coverage.covered) == 50
    assert coverage.ratio == 1.0


def test_every_entry_has_source_and_review_date() -> None:
    for notice in MAGIC_KINGDOM_SAFETY_NOTICES:
        assert notice.source_url.startswith(
            (
                "https://disneyworld.disney.go.com/attractions/magic-kingdom/",
                "https://disneyworld.disney.go.com/entertainment/magic-kingdom/",
            )
        )
        assert notice.reviewed_on.isoformat() <= NOTICE_CORPUS_VERSION
        assert notice.evidence


def test_every_entry_has_exactly_one_mobility_access_class() -> None:
    mobility_codes = {
        "transfer-from-wheelchair",
        "transfer-to-wheelchair-then-ride",
        "ambulatory",
        "transfer-to-wheelchair",
        "wheelchair-accessibility",
    }
    for notice in MAGIC_KINGDOM_SAFETY_NOTICES:
        assert len(mobility_codes.intersection(notice.evidence)) == 1, (
            notice.attraction_id
        )


def test_every_entry_flags_exactly_what_its_evidence_publishes() -> None:
    for notice in MAGIC_KINGDOM_SAFETY_NOTICES:
        expected = set().union(*(_EVIDENCE_FLAGS[code] for code in notice.evidence))

        assert notice.flags == expected, notice.attraction_id


def test_published_notices_spot_check() -> None:
    store = magic_kingdom_knowledge_store()

    # Spelled out, not frozenset(RideRestriction): a new enum member must not
    # silently change what this spot check asserts.
    assert store.notice_for(SPACE_MOUNTAIN) == frozenset(
        {
            HIGH_G,
            RideRestriction.NOT_RECOMMENDED_MOTION_SENSITIVITY,
            RideRestriction.NOT_RECOMMENDED_HEART_CONDITION,
            RideRestriction.NOT_RECOMMENDED_BACK_NECK,
            RideRestriction.NOT_RECOMMENDED_EXPECTANT,
            TRANSFER,
            RideRestriction.USES_SERVICE_ANIMAL,
        }
    )
    assert store.notice_for(SEVEN_DWARFS) == frozenset(
        {
            RideRestriction.NOT_RECOMMENDED_EXPECTANT,
            TRANSFER,
            RideRestriction.USES_SERVICE_ANIMAL,
        }
    )
    assert store.notice_for(SMALL_WORLD) == frozenset()
    # A show page publishes "may remain in wheelchair/ECV" and no warning: a notice
    # on file that restricts nothing, not a missing notice.
    assert store.notice_for(HAPPILY_EVER_AFTER) == frozenset()
