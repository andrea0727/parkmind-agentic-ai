"""Spoken attraction names -> catalog node ids, failing closed."""

import pytest
from elicit_support import FakeAttractionRepository, make_attraction, make_names

from parkmind.services.use_cases.resolve_attraction_names import (
    AttractionNameResolver,
    CatalogUnavailableError,
)


def test_exact_match_ignores_case_and_spacing() -> None:
    resolution = make_names().resolve(["TRON", "  space   MOUNTAIN "])

    assert resolution.complete
    assert resolution.resolved["TRON"].node_id == "id-tron"
    assert resolution.resolved["  space   MOUNTAIN "].name == "Space Mountain"


def test_a_unique_partial_name_resolves() -> None:
    resolution = make_names().resolve(["Splash", "haunted"])

    assert resolution.node_ids(["Splash", "haunted"]) == ["id-splash", "id-haunted"]


def test_a_partial_name_shared_by_several_attractions_is_ambiguous_not_guessed() -> (
    None
):
    resolution = make_names().resolve(["Mountain"])

    assert not resolution.complete
    assert resolution.resolved == {}
    assert resolution.ambiguous["Mountain"] == (
        "Big Thunder Mountain",
        "Space Mountain",
        "Splash Mountain",
    )


def test_an_exact_name_wins_over_longer_names_that_contain_it() -> None:
    names = make_names()
    resolution = names.resolve(["TRON"])

    assert resolution.complete and resolution.resolved["TRON"].node_id == "id-tron"


def test_an_unknown_name_is_reported() -> None:
    resolution = make_names().resolve(["TRON", "Death Star", ""])

    assert resolution.unknown == ("Death Star", "")
    assert not resolution.complete
    assert "TRON" in resolution.resolved


@pytest.mark.parametrize(
    ("official", "spoken"),
    [
        ("Peter Pan's Flight", ["Peter Pan", "peter pan flight", "Peter Pan’s Flight"]),
        (
            '"it\'s a small world"',
            ["small world", "it's a small world", "it’s a small world"],
        ),
        (
            "Buzz Lightyear’s Space Ranger Spin",
            ["Buzz Lightyear", "buzz lightyear's space ranger spin"],
        ),
        ("Winnie-the-Pooh", ["winnie the pooh", "Winnie-the-Pooh"]),
    ],
)
def test_punctuation_and_possessives_do_not_stop_a_name_from_resolving(
    official: str, spoken: list[str]
) -> None:
    catalog = [
        make_attraction("id-target", official),
        make_attraction("id-other", "Haunted Mansion"),
    ]

    resolution = make_names(catalog).resolve(spoken)

    assert resolution.complete, resolution
    assert {r.node_id for r in resolution.resolved.values()} == {"id-target"}


def test_normalizing_does_not_merge_different_attractions() -> None:
    catalog = [
        make_attraction("id-a", "Peter Pan's Flight"),
        make_attraction("id-b", "Peter Pan Mini Golf"),
    ]

    resolution = make_names(catalog).resolve(["Peter Pan"])

    assert resolution.resolved == {}
    assert resolution.ambiguous["Peter Pan"] == (
        "Peter Pan Mini Golf",
        "Peter Pan's Flight",
    )


def test_a_spelling_variant_does_not_hide_an_ambiguous_name() -> None:
    catalog = [
        make_attraction("id-regal", "Prince Charming Regal Carrousel"),
        make_attraction("id-progress", "Walt Disney's Carousel of Progress"),
    ]

    resolution = make_names(catalog).resolve(["Carousel"])

    assert resolution.resolved == {}
    assert resolution.ambiguous["Carousel"] == (
        "Prince Charming Regal Carrousel",
        "Walt Disney's Carousel of Progress",
    )


def test_node_ids_are_deduplicated_in_order() -> None:
    resolution = make_names().resolve(["tron", "TRON", "Teacups"])

    assert resolution.node_ids(["tron", "TRON", "Teacups"]) == ["id-tron", "id-teacups"]


def test_no_names_means_no_catalog_access() -> None:
    class Exploding:
        def list_attractions(self, park_id: str):
            raise AssertionError("catalog must not be read")

    resolution = AttractionNameResolver(Exploding(), "mk").resolve([])  # type: ignore[arg-type]

    assert resolution.complete and resolution.resolved == {}


def test_an_empty_catalog_fails_instead_of_calling_every_name_unknown() -> None:
    with pytest.raises(CatalogUnavailableError):
        make_names([]).resolve(["TRON"])


def test_another_park_has_no_catalog() -> None:
    resolver = AttractionNameResolver(FakeAttractionRepository(), "other")  # type: ignore[arg-type]

    with pytest.raises(CatalogUnavailableError):
        resolver.resolve(["TRON"])
