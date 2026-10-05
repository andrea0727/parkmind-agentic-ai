"""Curated Magic Kingdom catalog: shows, meet-and-greets and exclusions (issue #69)."""

from parkmind.core.contracts import AttractionCategory
from parkmind.services.clients.land_reference_data import MAGIC_KINGDOM_LANDS
from parkmind.services.clients.themeparks_reference_data import (
    MAGIC_KINGDOM_ATTRACTION_METADATA,
    MAGIC_KINGDOM_EXCLUDED_ENTITIES,
    MAGIC_KINGDOM_SCHEDULED_SHOWS,
    PROVISIONAL_TYPICAL_WAITS,
)

CURATED = MAGIC_KINGDOM_ATTRACTION_METADATA


def test_every_curated_land_is_canonical() -> None:
    """A typo'd ``land`` value would silently strand an attraction: no alias

    maps to it and no error is raised. Every curated ``land`` must be one of
    the canonical names in ``MAGIC_KINGDOM_LANDS``.
    """
    assert {meta["land"] for meta in CURATED.values()} <= MAGIC_KINGDOM_LANDS


def test_every_scheduled_show_is_a_curated_show_with_no_queue() -> None:
    assert len(MAGIC_KINGDOM_SCHEDULED_SHOWS) == 9
    for show_id in MAGIC_KINGDOM_SCHEDULED_SHOWS:
        assert CURATED[show_id]["category"] is AttractionCategory.SHOW
        assert CURATED[show_id]["typical_wait_minutes"] == 0


def test_meet_and_greets_are_characters_with_provisional_waits() -> None:
    meets = {
        i
        for i, meta in CURATED.items()
        if meta["category"] is AttractionCategory.CHARACTER
    }

    assert len(meets) == 6
    assert meets == PROVISIONAL_TYPICAL_WAITS
    assert all(CURATED[i]["typical_wait_minutes"] > 0 for i in meets)


def test_excluded_entities_are_never_curated_and_say_why() -> None:
    assert not set(MAGIC_KINGDOM_EXCLUDED_ENTITIES) & set(CURATED)
    assert all(
        reason.startswith("party-only")
        for reason in MAGIC_KINGDOM_EXCLUDED_ENTITIES.values()
    )


def test_theater_attractions_are_not_scheduled_shows() -> None:
    """Category SHOW alone doesn't make a scheduled show: The Hall of Presidents is a
    continuous theater attraction with no showtimes."""
    hall_of_presidents = "2ebfb38c-5cb5-4de1-86c0-f7af14188022"

    assert CURATED[hall_of_presidents]["category"] is AttractionCategory.SHOW
    assert hall_of_presidents not in MAGIC_KINGDOM_SCHEDULED_SHOWS
