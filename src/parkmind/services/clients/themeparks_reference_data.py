"""Curated attraction metadata for Magic Kingdom Park (ThemeParks Wiki).

ThemeParks Wiki's `/entity/{id}/children` response gives identity fields
only (id, name, entityType) — it has no category, height restriction,
typical wait, outdoor flag, or land assignment for any entity. Those five
fields are hand-curated here from public, well-documented attraction
facts (height requirements cross-checked against park.fan's
`minimumHeight` field, which is mislabeled "in" but actually reports
centimeters — verified against 7 attractions' published height
requirements before trusting it).

Scope: the 35 permanent ATTRACTION-type entities (captured 2026-09-17), plus the
15 SHOW-type entities a day guest can plan around (captured 2026-09-24, re-checked
2026-09-29; issue #69), all keyed by their real ThemeParks Wiki entity id:

- 9 scheduled shows (parades, fireworks, street musicians, stage shows) -- category
  ``SHOW``, listed in ``MAGIC_KINGDOM_SCHEDULED_SHOWS``. Their ``/live`` showtimes
  are ``Performance Time`` starts, so coverage expects showtimes while they operate.
- 6 character meet-and-greets -- category ``CHARACTER``. The provider reports them as
  SHOWs, but they are an open window with a STANDBY line, not a scheduled start: plan
  them like an attraction stop (rule 3 needs showtimes, they have none). Their
  typical waits are provisional (``PROVISIONAL_TYPICAL_WAITS``).

Not curated on purpose:

- ``MAGIC_KINGDOM_EXCLUDED_ENTITIES``: party-only / separately ticketed entities (12 SHOWs and 2 ATTRACTIONs)
  (Mickey's Not-So-Scary Halloween Party, Disney After Hours). ``parse_catalog`` skips
  them without reporting an issue; the list is seasonal and must be revisited.
- Jessie's Roundup: its only ``Operating`` window is dated 2026-09-08 (stale) and it
  has no queue, so it stays reported as ``MISSING_METADATA`` until it's clear whether
  it still runs.

Any other provider id missing from this table is reported as ``MISSING_METADATA``
and left out of the catalog -- the designed degradation path.
"""

from typing import TypedDict

from parkmind.core.contracts import AttractionCategory


class AttractionMetadata(TypedDict):
    category: AttractionCategory
    height_restriction_cm: int | None
    typical_wait_minutes: int
    outdoor: bool
    land: str


MAGIC_KINGDOM_ATTRACTION_METADATA: dict[str, AttractionMetadata] = {
    # Main Street, U.S.A.
    "e39b831b-7731-49bb-815b-289b4f49a9fd": {  # Walt Disney World Railroad - Main Street
        "category": AttractionCategory.TRANSPORT,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    "888fb4a4-7adf-47a1-8ba2-c258cc64fd75": {  # Main Street Vehicles
        "category": AttractionCategory.TRANSPORT,
        "height_restriction_cm": None,
        "typical_wait_minutes": 5,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    # Adventureland
    "de737ffc-306b-4f32-8bbb-34e5d370ec8f": {  # A Pirate's Adventure
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 15,
        "outdoor": True,
        "land": "Adventureland",
    },
    "30fe3c64-af71-4c66-a54b-aa61fd7af177": {  # Swiss Family Treehouse
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": True,
        "land": "Adventureland",
    },
    "96455de6-f4f1-403c-9391-bf8396979149": {  # The Magic Carpets of Aladdin
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": True,
        "land": "Adventureland",
    },
    "6fd1e225-53a0-4a80-a577-4bbc9a471075": {  # Walt Disney's Enchanted Tiki Room
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": False,
        "land": "Adventureland",
    },
    "352feb94-e52e-45eb-9c92-e4b44c6b1a9d": {  # Pirates of the Caribbean
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 30,
        "outdoor": False,
        "land": "Adventureland",
    },
    "796b0a25-c51e-456e-9bb8-50a324e301b3": {  # Jungle Cruise
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 35,
        "outdoor": True,
        "land": "Adventureland",
    },
    # Frontierland
    "de3309ca-97d5-4211-bffe-739fed47e92f": {  # Big Thunder Mountain Railroad
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 102,  # park.fan minimumHeight=102 (its "in" label is wrong; this is cm)
        "typical_wait_minutes": 45,
        "outdoor": True,
        "land": "Frontierland",
    },
    "0f57cecf-5502-4503-8bc3-ba84d3708ace": {  # Country Bear Musical Jamboree
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": False,
        "land": "Frontierland",
    },
    "73cb9445-0695-47a3-87ce-d08ae36b5f3c": {  # Tiana's Bayou Adventure
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 102,  # park.fan minimumHeight=102 (cm, not "in")
        "typical_wait_minutes": 55,
        "outdoor": True,
        "land": "Frontierland",
    },
    # Liberty Square
    "2551a77d-023f-4ab1-9a19-8afec0190f39": {  # Haunted Mansion
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 35,
        "outdoor": False,
        "land": "Liberty Square",
    },
    "2ebfb38c-5cb5-4de1-86c0-f7af14188022": {  # The Hall of Presidents
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": False,
        "land": "Liberty Square",
    },
    # Fantasyland
    "924a3b2c-6b4b-49e5-99d3-e9dc3f2e8a48": {  # The Barnstormer
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": 89,  # park.fan minimumHeight=89 (cm, not "in")
        "typical_wait_minutes": 20,
        "outdoor": True,
        "land": "Fantasyland",
    },
    "e40ac396-cbac-43f4-8752-764ed60ccceb": {  # WDW Railroad - Fantasyland
        "category": AttractionCategory.TRANSPORT,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": True,
        "land": "Fantasyland",
    },
    "9d4d5229-7142-44b6-b4fb-528920969a2c": {  # Seven Dwarfs Mine Train
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 97,  # park.fan minimumHeight=97 (cm, not "in")
        "typical_wait_minutes": 60,
        "outdoor": True,
        "land": "Fantasyland",
    },
    "e76c93df-31af-49a5-8e2f-752c76c937c9": {  # Enchanted Tales with Belle
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": False,
        "land": "Fantasyland",
    },
    "f5aad2d4-a419-4384-bd9a-42f86385c750": {  # "it's a small world"
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": False,
        "land": "Fantasyland",
    },
    "890fa430-89c0-4a3f-96c9-11597888005e": {  # Dumbo the Flying Elephant
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 25,
        "outdoor": True,
        "land": "Fantasyland",
    },
    "86a41273-5f15-4b54-93b6-829f140e5161": {  # Peter Pan's Flight
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 50,
        "outdoor": False,
        "land": "Fantasyland",
    },
    "f010bc01-b450-4476-a5f3-a5f2813104b2": {  # Casey Jr. Splash 'N' Soak Station
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 5,
        "outdoor": True,
        "land": "Fantasyland",
    },
    "273ddb8d-e7b5-4e34-8657-1113f49262a5": {  # Prince Charming Regal Carrousel
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 15,
        "outdoor": True,
        "land": "Fantasyland",
    },
    "0aae716c-af13-4439-b638-d75fb1649df3": {  # Mad Tea Party
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": True,
        "land": "Fantasyland",
    },
    "7c5e1e02-3a44-4151-9005-44066d5ba1da": {  # Mickey's PhilharMagic
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": False,
        "land": "Fantasyland",
    },
    "0d94ad60-72f0-4551-83a6-ebaecdd89737": {  # The Many Adventures of Winnie the Pooh
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 30,
        "outdoor": False,
        "land": "Fantasyland",
    },
    "3cba0cb4-e2a6-402c-93ee-c11ffcb127ef": {  # Under the Sea - Journey of The Little Mermaid
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 25,
        "outdoor": False,
        "land": "Fantasyland",
    },
    "90d79335-c907-4069-a021-d0fe1ec73ae2": {  # Cinderella Castle (walkthrough)
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Fantasyland",
    },
    # Tomorrowland
    "f163ddcd-43e1-488d-8276-2381c1db0a39": {  # Tomorrowland Speedway
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": 81,  # park.fan minimumHeight=81 (cm, not "in"); to drive
        "typical_wait_minutes": 20,
        "outdoor": True,
        "land": "Tomorrowland",
    },
    "b2260923-9315-40fd-9c6b-44dd811dbe64": {  # Space Mountain
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 112,  # park.fan minimumHeight=113 (cm, not "in"); published 44in
        "typical_wait_minutes": 60,
        "outdoor": False,
        "land": "Tomorrowland",
    },
    "8183f3f2-1b59-4b9c-b634-6a863bdf8d84": {  # Walt Disney's Carousel of Progress
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": False,
        "land": "Tomorrowland",
    },
    "ffcfeaa2-1416-4920-a1ed-543c1a1695c4": {  # Tomorrowland Transit Authority PeopleMover
        "category": AttractionCategory.TRANSPORT,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": True,
        "land": "Tomorrowland",
    },
    "e8f0b426-7645-4ea3-8b41-b94ae7091a41": {  # Monsters, Inc. Laugh Floor
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 15,
        "outdoor": False,
        "land": "Tomorrowland",
    },
    "d9d12438-d999-4482-894b-8955fdb20ccf": {  # Astro Orbiter
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": True,
        "land": "Tomorrowland",
    },
    "5a43d1a7-ad53-4d25-abfe-25625f0da304": {  # TRON Lightcycle / Run
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 122,  # park.fan minimumHeight=122 (cm, not "in"); published 48in
        "typical_wait_minutes": 75,
        "outdoor": True,
        "land": "Tomorrowland",
    },
    "72c7343a-f7fb-4f66-95df-c91016de7338": {  # Buzz Lightyear's Space Ranger Spin
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 25,
        "outdoor": False,
        "land": "Tomorrowland",
    },
    # Scheduled shows (Performance Time starts; issue #69)
    "a0613b70-293f-4a5b-8169-357be1777c62": {  # Casey's Corner Pianist
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    "f819079e-644e-4fce-bda3-26b899ac7027": {  # Disney Adventure Friends Cavalcade
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    "ee56b2f3-fd49-4a29-ae1a-2d321549a633": {  # Disney Festival of Fantasy Parade
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    "d69261dc-62b8-434c-83bd-93649b43c408": {  # Disney Starlight: Dream the Night Away
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    "1c708beb-41e1-43ae-8dd8-1e85075aeb38": {  # Flag Retreat
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    "22b78ed9-a692-47cb-b6a4-6d1224ff67e3": {  # Happily Ever After
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    "51392ca4-f824-42d8-8808-8110ec8e0e22": {  # Main Street Philharmonic
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    "4c31b3ad-5dc9-437f-ac1a-0fdff36a2818": {  # Mickey's Magical Friendship Faire
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    "1eee22e8-1d0a-4809-a42b-df3ae55c69d5": {  # The Dapper Dans
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
        "land": "Main Street, U.S.A.",
    },
    # Character meet-and-greets (Operating window + STANDBY line; plan as attraction stops)
    "012a211b-4c91-451c-8a0e-5e3ab398eda8": {  # Meet Ariel at Her Grotto
        "category": AttractionCategory.CHARACTER,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,  # provisional
        "outdoor": True,  # outdoor line
        "land": "Fantasyland",
    },
    "40737d3d-0ff6-4a9e-a050-beb87bf90120": {  # Meet Cinderella and a Visiting Princess at Princess Fairytale Hall
        "category": AttractionCategory.CHARACTER,
        "height_restriction_cm": None,
        "typical_wait_minutes": 25,  # provisional
        "outdoor": False,  # indoor hall
        "land": "Fantasyland",
    },
    "cf4b2ba4-3626-4de7-9d07-abe8a65b1665": {  # Meet Princess Tiana and a Visiting Princess at Princess Fairytale Hall
        "category": AttractionCategory.CHARACTER,
        "height_restriction_cm": None,
        "typical_wait_minutes": 25,  # provisional
        "outdoor": False,  # indoor hall
        "land": "Fantasyland",
    },
    "166f2985-7b27-4eff-a8b3-29c3448ba198": {  # Meet Daring Disney Pals as Circus Stars at Pete's Silly Sideshow
        "category": AttractionCategory.CHARACTER,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,  # provisional
        "outdoor": True,  # unsure: tent, partly outdoor line
        "land": "Fantasyland",
    },
    "b5d6d1d1-e960-4c8f-a8a4-b9748b386b64": {  # Meet Dashing Disney Pals as Circus Stars at Pete's Silly Sideshow
        "category": AttractionCategory.CHARACTER,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,  # provisional
        "outdoor": True,  # unsure: tent, partly outdoor line
        "land": "Fantasyland",
    },
    "a2d92647-634d-4eb4-886b-9da858e871f1": {  # Meet Mickey at Town Square Theater
        "category": AttractionCategory.CHARACTER,
        "height_restriction_cm": None,
        "typical_wait_minutes": 30,  # provisional
        "outdoor": False,  # indoor theater
        "land": "Main Street, U.S.A.",
    },
}


MAGIC_KINGDOM_SCHEDULED_SHOWS: frozenset[str] = frozenset(
    {
        "a0613b70-293f-4a5b-8169-357be1777c62",  # Casey's Corner Pianist
        "f819079e-644e-4fce-bda3-26b899ac7027",  # Disney Adventure Friends Cavalcade
        "ee56b2f3-fd49-4a29-ae1a-2d321549a633",  # Disney Festival of Fantasy Parade
        "d69261dc-62b8-434c-83bd-93649b43c408",  # Disney Starlight: Dream the Night Away
        "1c708beb-41e1-43ae-8dd8-1e85075aeb38",  # Flag Retreat
        "22b78ed9-a692-47cb-b6a4-6d1224ff67e3",  # Happily Ever After
        "51392ca4-f824-42d8-8808-8110ec8e0e22",  # Main Street Philharmonic
        "4c31b3ad-5dc9-437f-ac1a-0fdff36a2818",  # Mickey's Magical Friendship Faire
        "1eee22e8-1d0a-4809-a42b-df3ae55c69d5",  # The Dapper Dans
    }
)
"""Curated entities that run on ``Performance Time`` starts. Coverage expects
showtimes for each one that is OPERATING (Architecture 8.1: "every relevant show
has showtimes"). Decided from this curated list, never from the live payload's
``kind``, so a show that vanishes from ``/live`` still counts as missing."""

PROVISIONAL_TYPICAL_WAITS: frozenset[str] = frozenset(
    {
        "012a211b-4c91-451c-8a0e-5e3ab398eda8",  # Meet Ariel at Her Grotto
        "40737d3d-0ff6-4a9e-a050-beb87bf90120",  # Meet Cinderella and a Visiting Princess at Princess Fairytale Hall
        "cf4b2ba4-3626-4de7-9d07-abe8a65b1665",  # Meet Princess Tiana and a Visiting Princess at Princess Fairytale Hall
        "166f2985-7b27-4eff-a8b3-29c3448ba198",  # Meet Daring Disney Pals as Circus Stars at Pete's Silly Sideshow
        "b5d6d1d1-e960-4c8f-a8a4-b9748b386b64",  # Meet Dashing Disney Pals as Circus Stars at Pete's Silly Sideshow
        "a2d92647-634d-4eb4-886b-9da858e871f1",  # Meet Mickey at Town Square Theater
    }
)
"""Ids whose ``typical_wait_minutes`` is a conservative guess, not a measured value.
Replace each with the average STANDBY wait from collected snapshots (P0-11) once
enough have been stored."""

_MNSSHP = "party-only: Mickey's Not-So-Scary Halloween Party (separate ticket)"
_AFTER_HOURS = "party-only: Disney After Hours (separate ticket)"

MAGIC_KINGDOM_EXCLUDED_ENTITIES: dict[str, str] = {
    "c1f39c15-7845-46b5-b6fd-2ae368a32a37": _MNSSHP,  # Captain Jack's Buccaneer Bash at Mickey's Not-So-Scary Halloween Party
    "9140033d-d746-464b-acad-f05f9357f0bd": _MNSSHP,  # Character Greetings at Mickey's Not-So-Scary Halloween Party
    "9221e600-6715-4b4f-b39a-00c318c00e03": _MNSSHP,  # Destination DescenDANCE Party at Mickey's Not-So-Scary Halloween Party
    "86c198b0-7c02-4354-814a-27ad70067d45": _AFTER_HOURS,  # Disney Enchantment at Disney After Hours at Magic Kingdom
    "05ca3e51-580b-44ca-8046-7d3e57a6d248": _MNSSHP,  # Disney's Not-So-Spooky Spectacular at Mickey's Not-So-Scary Halloween Party
    "6d74b3d9-f977-4c9b-8114-93969b51b105": _MNSSHP,  # Hocus Pocus Villain Spelltacular
    "a74421f2-5de4-4425-bd06-8b4639b29826": _MNSSHP,  # Meet Festive Disney Pals at Mickey's Not-So-Scary Halloween Party
    "947edbee-85a5-4aa0-99f3-14c7de539542": _MNSSHP,  # Meet Jack Skellington and Sally at Mickey's Not-So-Scary Halloween Party
    "5564113f-25b4-4646-ac35-aacf983e2fd6": _MNSSHP,  # Meet Mickey Mouse and Minnie Mouse at Mickey's Not-So-Scary Halloween Party
    "5c00cd7c-b207-4d9d-9c8c-a8d418fc5425": _MNSSHP,  # Mickey's Boo-To-You Halloween Parade at Mickey's Not-So-Scary Halloween Party
    "97c837c3-4a0f-4d52-b93c-074cf31d6f41": _MNSSHP,  # Stitch's Masquerade Mashup at Mickey's Not-So-Scary Halloween Party
    "92524eb7-4ee5-4eab-936c-2eb8e6eb0ecd": _MNSSHP,  # The Cadaver Dans Barbershop Quartet at Mickey's Not-So-Scary Halloween Party
    "52ac3730-b955-452a-a7cc-17e3b06182ac": _MNSSHP,  # Trick-or-Treat Locations at Mickey's Not-So-Scary Halloween Party (ATTRACTION)
    "362aa1ba-b1a2-4617-a97c-5a6805f32417": _MNSSHP,  # Allergy Request Trick-or-Treating Experience at Mickey's Not-So-Scary Halloween Party (ATTRACTION)
}
"""Provider entities left out of the catalog on purpose, with the reason.
``parse_catalog`` skips them without a ``MISSING_METADATA`` issue. Seasonal: review
it when the party calendar changes (a new party entity is still reported as missing)."""
