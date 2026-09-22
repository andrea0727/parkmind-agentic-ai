"""Curated attraction metadata for Magic Kingdom Park (ThemeParks Wiki).

ThemeParks Wiki's `/entity/{id}/children` response gives identity fields
only (id, name, entityType) — it has no category, height restriction,
typical wait, or outdoor flag for any entity. Those four fields are hand
-curated here from public, well-documented attraction facts (height
requirements cross-checked against park.fan's `minimumHeight` field, which
is mislabeled "in" but actually reports centimeters — verified against 7
attractions' published height requirements before trusting it).

Scope: the 35 permanent ATTRACTION-type entities only, keyed by their real
ThemeParks Wiki entity id (captured 2026-09-17). SHOW-type entities
(parades, fireworks, character meet-and-greets, seasonal event
entertainment) are intentionally NOT curated here yet — the real capture
this table is built from included several one-off entries tied to a
running seasonal event ("Mickey's Not-So-Scary Halloween Party"), and
sorting permanent shows from seasonal ones needs a follow-up pass rather
than a guess. ThemeParksClient.get_catalog() skips any entity id missing
from this table (with a warning), so SHOW entities are simply omitted from
the catalog until this table is extended — that's the designed
degradation path, not a bug.
"""

from typing import TypedDict

from parkmind.core.contracts import AttractionCategory


class AttractionMetadata(TypedDict):
    category: AttractionCategory
    height_restriction_cm: int | None
    typical_wait_minutes: int
    outdoor: bool


MAGIC_KINGDOM_ATTRACTION_METADATA: dict[str, AttractionMetadata] = {
    # Main Street, U.S.A.
    "e39b831b-7731-49bb-815b-289b4f49a9fd": {  # Walt Disney World Railroad - Main Street
        "category": AttractionCategory.TRANSPORT,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": True,
    },
    "888fb4a4-7adf-47a1-8ba2-c258cc64fd75": {  # Main Street Vehicles
        "category": AttractionCategory.TRANSPORT,
        "height_restriction_cm": None,
        "typical_wait_minutes": 5,
        "outdoor": True,
    },
    # Adventureland
    "de737ffc-306b-4f32-8bbb-34e5d370ec8f": {  # A Pirate's Adventure
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 15,
        "outdoor": True,
    },
    "30fe3c64-af71-4c66-a54b-aa61fd7af177": {  # Swiss Family Treehouse
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": True,
    },
    "96455de6-f4f1-403c-9391-bf8396979149": {  # The Magic Carpets of Aladdin
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": True,
    },
    "6fd1e225-53a0-4a80-a577-4bbc9a471075": {  # Walt Disney's Enchanted Tiki Room
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": False,
    },
    "352feb94-e52e-45eb-9c92-e4b44c6b1a9d": {  # Pirates of the Caribbean
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 30,
        "outdoor": False,
    },
    "796b0a25-c51e-456e-9bb8-50a324e301b3": {  # Jungle Cruise
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 35,
        "outdoor": True,
    },
    # Frontierland
    "de3309ca-97d5-4211-bffe-739fed47e92f": {  # Big Thunder Mountain Railroad
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 102,  # park.fan minimumHeight=102 (its "in" label is wrong; this is cm)
        "typical_wait_minutes": 45,
        "outdoor": True,
    },
    "0f57cecf-5502-4503-8bc3-ba84d3708ace": {  # Country Bear Musical Jamboree
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": False,
    },
    "73cb9445-0695-47a3-87ce-d08ae36b5f3c": {  # Tiana's Bayou Adventure
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 102,  # park.fan minimumHeight=102 (cm, not "in")
        "typical_wait_minutes": 55,
        "outdoor": True,
    },
    # Liberty Square
    "2551a77d-023f-4ab1-9a19-8afec0190f39": {  # Haunted Mansion
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 35,
        "outdoor": False,
    },
    "2ebfb38c-5cb5-4de1-86c0-f7af14188022": {  # The Hall of Presidents
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": False,
    },
    # Fantasyland
    "924a3b2c-6b4b-49e5-99d3-e9dc3f2e8a48": {  # The Barnstormer
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": 89,  # park.fan minimumHeight=89 (cm, not "in")
        "typical_wait_minutes": 20,
        "outdoor": True,
    },
    "e40ac396-cbac-43f4-8752-764ed60ccceb": {  # WDW Railroad - Fantasyland
        "category": AttractionCategory.TRANSPORT,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": True,
    },
    "9d4d5229-7142-44b6-b4fb-528920969a2c": {  # Seven Dwarfs Mine Train
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 97,  # park.fan minimumHeight=97 (cm, not "in")
        "typical_wait_minutes": 60,
        "outdoor": True,
    },
    "e76c93df-31af-49a5-8e2f-752c76c937c9": {  # Enchanted Tales with Belle
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": False,
    },
    "f5aad2d4-a419-4384-bd9a-42f86385c750": {  # "it's a small world"
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": False,
    },
    "890fa430-89c0-4a3f-96c9-11597888005e": {  # Dumbo the Flying Elephant
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 25,
        "outdoor": True,
    },
    "86a41273-5f15-4b54-93b6-829f140e5161": {  # Peter Pan's Flight
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 50,
        "outdoor": False,
    },
    "f010bc01-b450-4476-a5f3-a5f2813104b2": {  # Casey Jr. Splash 'N' Soak Station
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 5,
        "outdoor": True,
    },
    "273ddb8d-e7b5-4e34-8657-1113f49262a5": {  # Prince Charming Regal Carrousel
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 15,
        "outdoor": True,
    },
    "0aae716c-af13-4439-b638-d75fb1649df3": {  # Mad Tea Party
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": True,
    },
    "7c5e1e02-3a44-4151-9005-44066d5ba1da": {  # Mickey's PhilharMagic
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": False,
    },
    "0d94ad60-72f0-4551-83a6-ebaecdd89737": {  # The Many Adventures of Winnie the Pooh
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 30,
        "outdoor": False,
    },
    "3cba0cb4-e2a6-402c-93ee-c11ffcb127ef": {  # Under the Sea - Journey of The Little Mermaid
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 25,
        "outdoor": False,
    },
    "90d79335-c907-4069-a021-d0fe1ec73ae2": {  # Cinderella Castle (walkthrough)
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 0,
        "outdoor": True,
    },
    # Tomorrowland
    "f163ddcd-43e1-488d-8276-2381c1db0a39": {  # Tomorrowland Speedway
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": 81,  # park.fan minimumHeight=81 (cm, not "in"); to drive
        "typical_wait_minutes": 20,
        "outdoor": True,
    },
    "b2260923-9315-40fd-9c6b-44dd811dbe64": {  # Space Mountain
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 112,  # park.fan minimumHeight=113 (cm, not "in"); published 44in
        "typical_wait_minutes": 60,
        "outdoor": False,
    },
    "8183f3f2-1b59-4b9c-b634-6a863bdf8d84": {  # Walt Disney's Carousel of Progress
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": False,
    },
    "ffcfeaa2-1416-4920-a1ed-543c1a1695c4": {  # Tomorrowland Transit Authority PeopleMover
        "category": AttractionCategory.TRANSPORT,
        "height_restriction_cm": None,
        "typical_wait_minutes": 10,
        "outdoor": True,
    },
    "e8f0b426-7645-4ea3-8b41-b94ae7091a41": {  # Monsters, Inc. Laugh Floor
        "category": AttractionCategory.SHOW,
        "height_restriction_cm": None,
        "typical_wait_minutes": 15,
        "outdoor": False,
    },
    "d9d12438-d999-4482-894b-8955fdb20ccf": {  # Astro Orbiter
        "category": AttractionCategory.FAMILY,
        "height_restriction_cm": None,
        "typical_wait_minutes": 20,
        "outdoor": True,
    },
    "5a43d1a7-ad53-4d25-abfe-25625f0da304": {  # TRON Lightcycle / Run
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 122,  # park.fan minimumHeight=122 (cm, not "in"); published 48in
        "typical_wait_minutes": 75,
        "outdoor": True,
    },
    "72c7343a-f7fb-4f66-95df-c91016de7338": {  # Buzz Lightyear's Space Ranger Spin
        "category": AttractionCategory.DARK_RIDE,
        "height_restriction_cm": None,
        "typical_wait_minutes": 25,
        "outdoor": False,
    },
}
