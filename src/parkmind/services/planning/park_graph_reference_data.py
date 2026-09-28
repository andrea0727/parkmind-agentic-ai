"""Curated land/location aliases for Magic Kingdom Park.

Land node ids are synthetic (namespaced "land:...", not real ThemeParks Wiki
entity ids) since no land-level entity exists in the provider's data model --
land names only ever appear there as attraction groupings, never as
addressable entities in their own right.
"""

MAGIC_KINGDOM_LOCATION_ALIASES: dict[str, str] = {
    "main street usa": "land:main-street-usa",
    "main street, u.s.a.": "land:main-street-usa",
    "adventureland": "land:adventureland",
    "frontierland": "land:frontierland",
    "liberty square": "land:liberty-square",
    "fantasyland": "land:fantasyland",
    "tomorrowland": "land:tomorrowland",
}
