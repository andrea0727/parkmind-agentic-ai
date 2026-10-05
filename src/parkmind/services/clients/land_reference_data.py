"""Canonical lands of Magic Kingdom and alias registry for location queries.

The planning layer accepts human phrasings like ``"near Frontierland"`` or
``"in fantasy land"`` and resolves them to a canonical land name. Keeping
the alias table here, next to the attraction-metadata curation, means both
pieces of ground truth about park topology live in the same place.

Aliases are stored in their normalized form (lowercased, single-spaced,
with the leading locative prefix stripped — see
``ParkGraph.resolve_location``). Add new aliases in that same normalized
form so no runtime normalization is needed at lookup time.
"""

MAGIC_KINGDOM_LANDS: frozenset[str] = frozenset(
    {
        "Main Street, U.S.A.",
        "Adventureland",
        "Frontierland",
        "Liberty Square",
        "Fantasyland",
        "Tomorrowland",
    }
)


MAGIC_KINGDOM_LAND_ALIASES: dict[str, str] = {
    # Main Street, U.S.A.
    "main street": "Main Street, U.S.A.",
    "main street usa": "Main Street, U.S.A.",
    "main street u.s.a.": "Main Street, U.S.A.",
    "main street, u.s.a.": "Main Street, U.S.A.",
    # Adventureland
    "adventureland": "Adventureland",
    "adventure land": "Adventureland",
    # Frontierland
    "frontierland": "Frontierland",
    "frontier land": "Frontierland",
    # Liberty Square
    "liberty square": "Liberty Square",
    # Fantasyland
    "fantasyland": "Fantasyland",
    "fantasy land": "Fantasyland",
    # Tomorrowland
    "tomorrowland": "Tomorrowland",
    "tomorrow land": "Tomorrowland",
}
