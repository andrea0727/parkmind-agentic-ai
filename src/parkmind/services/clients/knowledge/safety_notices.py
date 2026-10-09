"""Magic Kingdom safety-notice corpus, version 2026-10-01 (section 30 [C14], P0-26a, #69).

Every entry is read from the entity's official page on
disneyworld.disney.go.com ("Safety, accessibility and guest policies" block;
the pages were served in Spanish, the text quoted below is Disney's own), for
all 50 entities of the curated catalog: the 35 attractions (``/attractions/``
pages, reviewed 2026-09-28) and the 15 shows and meet-and-greets
(``/entertainment/`` pages, reviewed 2026-10-01; issue #69). Each entry keeps its
page and the evidence codes it was derived from, so a reviewer can re-check it
against the page. Bump ``NOTICE_CORPUS_VERSION`` whenever an entry changes.

All 15 entertainment pages publish only "Puede seguir en silla de ruedas/ECV" and
no warning, so their notices are on file and restrict nothing -- which is not the
same as having no notice (that fails closed).

Mapping from the published wording to ``RideRestriction`` (section 12 [C15]):

==========================================================  ==========================
Published on the page                                        Flags
==========================================================  ==========================
"Debe transferirse desde silla de ruedas/ECV"                REQUIRES_TRANSFER_FROM_
(``transfer-from-wheelchair``)                               WHEELCHAIR
"Se debe transferir a silla de ruedas, luego, a vehiculo     REQUIRES_TRANSFER_FROM_
de la atraccion" (``transfer-to-wheelchair-then-ride``)      WHEELCHAIR
"Debes poder desplazarte" (``ambulatory``)                   REQUIRES_TRANSFER_FROM_
                                                             WHEELCHAIR
"Se debe transferir a silla de ruedas" (ECV users move to    (none)
a wheelchair; ``transfer-to-wheelchair``)
"Puede seguir en silla de ruedas/ECV"                        (none)
(``wheelchair-accessibility``)
"No Se Permiten Animales de Servicio", or "no se permiten    USES_SERVICE_ANIMAL
animales de servicio en algunas areas"                       (fail closed)
"Los Animales de Servicio Estan Permitidos con Precaucion"   (none)
"Las mujeres embarazadas no deben subir."                    NOT_RECOMMENDED_EXPECTANT
Rider warning: "debes estar en buena salud y libre de        HEART_CONDITION, BACK_NECK,
presion alta; problemas del corazon, la espalda o el         MOTION_SENSITIVITY,
cuello; mareos; u otras condiciones que podrian verse        EXPECTANT and HIGH_G_FORCE
agravadas por esta aventura. Las mujeres embarazadas no
deben subir."
==========================================================  ==========================

The park publishes no separate "high G-force" notice. ``NOT_RECOMMENDED_HIGH_G_FORCE``
is set where the full rider warning appears ("other conditions that could be
aggravated by this adventure"), the park's only intensity notice; this is the
conservative reading -- a guest with that flag is kept off every attraction
the park warns about, and nowhere else.

Known trade-off, kept on purpose (reviewed in PR #68): the warning is published
verbatim on five attractions -- Big Thunder Mountain Railroad, Tiana's Bayou
Adventure, Space Mountain, TRON Lightcycle / Run and Tomorrowland Speedway. The
Speedway is not a high-G ride, yet it carries ``NOT_RECOMMENDED_HIGH_G_FORCE``
because its notice is word for word the coasters' notice; telling them apart
would be our judgment, not the park's text. The cost is fail-closed (a guest with
that flag loses the Speedway), never fail-open. Splitting the warning into
intensity tiers is a product decision left as a follow-up.
"""

from dataclasses import dataclass
from datetime import date

from parkmind.core.contracts import RideRestriction

from .in_memory import InMemoryKnowledgeStore

NOTICE_CORPUS_VERSION = "2026-10-01"
ATTRACTION_PAGE = "https://disneyworld.disney.go.com/attractions/magic-kingdom/{slug}/"
ENTERTAINMENT_PAGE = (
    "https://disneyworld.disney.go.com/entertainment/magic-kingdom/{slug}/"
)
REVIEWED_ON = date(2026, 9, 28)

HIGH_G = RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE
MOTION = RideRestriction.NOT_RECOMMENDED_MOTION_SENSITIVITY
HEART = RideRestriction.NOT_RECOMMENDED_HEART_CONDITION
BACK_NECK = RideRestriction.NOT_RECOMMENDED_BACK_NECK
EXPECTANT = RideRestriction.NOT_RECOMMENDED_EXPECTANT
TRANSFER = RideRestriction.REQUIRES_TRANSFER_FROM_WHEELCHAIR
SERVICE_ANIMAL = RideRestriction.USES_SERVICE_ANIMAL


@dataclass(frozen=True)
class SafetyNotice:
    """One reviewed corpus entry. Adapter-internal: only ``flags`` crosses the port."""

    attraction_id: str
    source_url: str
    reviewed_on: date
    evidence: tuple[str, ...]
    flags: frozenset[RideRestriction]


def _notice(
    attraction_id: str,
    slug: str,
    *,
    evidence: tuple[str, ...],
    flags: tuple[RideRestriction, ...],
    page: str = ATTRACTION_PAGE,
    reviewed_on: date = REVIEWED_ON,
) -> SafetyNotice:
    return SafetyNotice(
        attraction_id=attraction_id,
        source_url=page.format(slug=slug),
        reviewed_on=reviewed_on,
        evidence=evidence,
        flags=frozenset(flags),
    )


MAGIC_KINGDOM_SAFETY_NOTICES: tuple[SafetyNotice, ...] = (
    _notice(
        "f5aad2d4-a419-4384-bd9a-42f86385c750",  # "it's a small world"
        "its-a-small-world",
        evidence=("transfer-to-wheelchair",),
        flags=(),
    ),
    _notice(
        "de737ffc-306b-4f32-8bbb-34e5d370ec8f",  # A Pirate's Adventure
        "pirates-adventures",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    _notice(
        "d9d12438-d999-4482-894b-8955fdb20ccf",  # Astro Orbiter
        "astro-orbiter",
        evidence=("transfer-from-wheelchair",),
        flags=(TRANSFER,),
    ),
    _notice(
        "de3309ca-97d5-4211-bffe-739fed47e92f",  # Big Thunder Mountain Railroad
        "big-thunder-mountain-railroad",
        evidence=(
            "transfer-to-wheelchair-then-ride",
            "no-service-animals",
            "rider-warning",
        ),
        flags=(HIGH_G, MOTION, HEART, BACK_NECK, EXPECTANT, TRANSFER, SERVICE_ANIMAL),
    ),
    _notice(
        "72c7343a-f7fb-4f66-95df-c91016de7338",  # Buzz Lightyear's Space Ranger Spin
        "buzz-lightyear-space-ranger-spin",
        evidence=("transfer-to-wheelchair",),
        flags=(),
    ),
    _notice(
        "f010bc01-b450-4476-a5f3-a5f2813104b2",  # Casey Jr. Splash 'N' Soak Station
        "casey-jr-splash-n-soak-station",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    _notice(
        "90d79335-c907-4069-a021-d0fe1ec73ae2",  # Cinderella Castle
        "cinderella-castle",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    _notice(
        "0f57cecf-5502-4503-8bc3-ba84d3708ace",  # Country Bear Musical Jamboree
        "country-bear-jamboree",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    _notice(
        "890fa430-89c0-4a3f-96c9-11597888005e",  # Dumbo the Flying Elephant
        "dumbo-the-flying-elephant",
        evidence=("transfer-from-wheelchair",),
        flags=(TRANSFER,),
    ),
    _notice(
        "e76c93df-31af-49a5-8e2f-752c76c937c9",  # Enchanted Tales with Belle
        "enchanted-tales-with-belle",
        evidence=("transfer-to-wheelchair",),
        flags=(),
    ),
    _notice(
        "2551a77d-023f-4ab1-9a19-8afec0190f39",  # Haunted Mansion
        "haunted-mansion",
        evidence=("transfer-from-wheelchair",),
        flags=(TRANSFER,),
    ),
    _notice(
        "796b0a25-c51e-456e-9bb8-50a324e301b3",  # Jungle Cruise
        "jungle-cruise",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    _notice(
        "0aae716c-af13-4439-b638-d75fb1649df3",  # Mad Tea Party
        "mad-tea-party",
        evidence=("transfer-from-wheelchair",),
        flags=(TRANSFER,),
    ),
    _notice(
        "888fb4a4-7adf-47a1-8ba2-c258cc64fd75",  # Main Street Vehicles
        "main-street-vehicles",
        evidence=("transfer-from-wheelchair",),
        flags=(TRANSFER,),
    ),
    _notice(
        "7c5e1e02-3a44-4151-9005-44066d5ba1da",  # Mickey's PhilharMagic
        "mickeys-philharmagic",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    _notice(
        "e8f0b426-7645-4ea3-8b41-b94ae7091a41",  # Monsters, Inc. Laugh Floor
        "monsters-inc-laugh-floor",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    _notice(
        "86a41273-5f15-4b54-93b6-829f140e5161",  # Peter Pan's Flight
        "peter-pan-flight",
        evidence=("ambulatory", "no-service-animals"),
        flags=(TRANSFER, SERVICE_ANIMAL),
    ),
    _notice(
        "352feb94-e52e-45eb-9c92-e4b44c6b1a9d",  # Pirates of the Caribbean
        "pirates-of-the-caribbean",
        evidence=("transfer-to-wheelchair-then-ride",),
        flags=(TRANSFER,),
    ),
    _notice(
        "273ddb8d-e7b5-4e34-8657-1113f49262a5",  # Prince Charming Regal Carrousel
        "prince-charming-regal-carrousel",
        evidence=("transfer-to-wheelchair", "service-animals-with-caution"),
        flags=(),
    ),
    _notice(
        "9d4d5229-7142-44b6-b4fb-528920969a2c",  # Seven Dwarfs Mine Train
        "seven-dwarfs-mine-train",
        evidence=(
            "transfer-from-wheelchair",
            "no-service-animals",
            "expectant-mothers",
        ),
        flags=(EXPECTANT, TRANSFER, SERVICE_ANIMAL),
    ),
    _notice(
        "b2260923-9315-40fd-9c6b-44dd811dbe64",  # Space Mountain
        "space-mountain",
        evidence=(
            "transfer-to-wheelchair-then-ride",
            "no-service-animals",
            "rider-warning",
        ),
        flags=(HIGH_G, MOTION, HEART, BACK_NECK, EXPECTANT, TRANSFER, SERVICE_ANIMAL),
    ),
    _notice(
        "30fe3c64-af71-4c66-a54b-aa61fd7af177",  # Swiss Family Treehouse
        "swiss-family-treehouse",
        evidence=("ambulatory",),
        flags=(TRANSFER,),
    ),
    _notice(
        "5a43d1a7-ad53-4d25-abfe-25625f0da304",  # TRON Lightcycle / Run
        "tron-lightcycle-run",
        evidence=(
            "transfer-to-wheelchair-then-ride",
            "no-service-animals",
            "rider-warning",
        ),
        flags=(HIGH_G, MOTION, HEART, BACK_NECK, EXPECTANT, TRANSFER, SERVICE_ANIMAL),
    ),
    _notice(
        "924a3b2c-6b4b-49e5-99d3-e9dc3f2e8a48",  # The Barnstormer
        "barnstormer-starring-great-goofini",
        evidence=(
            "transfer-from-wheelchair",
            "no-service-animals",
            "expectant-mothers",
        ),
        flags=(EXPECTANT, TRANSFER, SERVICE_ANIMAL),
    ),
    _notice(
        "2ebfb38c-5cb5-4de1-86c0-f7af14188022",  # The Hall of Presidents
        "hall-of-presidents",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    _notice(
        "96455de6-f4f1-403c-9391-bf8396979149",  # The Magic Carpets of Aladdin
        "magic-carpets-of-aladdin",
        evidence=("transfer-to-wheelchair",),
        flags=(),
    ),
    _notice(
        "0d94ad60-72f0-4551-83a6-ebaecdd89737",  # The Many Adventures of Winnie the Pooh
        "many-adventures-of-winnie-the-pooh",
        evidence=("transfer-to-wheelchair",),
        flags=(),
    ),
    _notice(
        "73cb9445-0695-47a3-87ce-d08ae36b5f3c",  # Tiana's Bayou Adventure
        "tianas-bayou-adventure",
        evidence=(
            "transfer-from-wheelchair",
            "no-service-animals-in-some-areas",
            "rider-warning",
        ),
        flags=(HIGH_G, MOTION, HEART, BACK_NECK, EXPECTANT, TRANSFER, SERVICE_ANIMAL),
    ),
    _notice(
        "f163ddcd-43e1-488d-8276-2381c1db0a39",  # Tomorrowland Speedway
        "tomorrowland-speedway",
        evidence=("transfer-from-wheelchair", "rider-warning"),
        flags=(HIGH_G, MOTION, HEART, BACK_NECK, EXPECTANT, TRANSFER),
    ),
    _notice(
        "ffcfeaa2-1416-4920-a1ed-543c1a1695c4",  # Tomorrowland Transit Authority PeopleMover
        "tomorrowland-transit-authority-peoplemover",
        evidence=("ambulatory",),
        flags=(TRANSFER,),
    ),
    _notice(
        "3cba0cb4-e2a6-402c-93ee-c11ffcb127ef",  # Under the Sea - Journey of The Little Mermaid
        "under-the-sea-journey-of-the-little-mermaid",
        evidence=("transfer-to-wheelchair",),
        flags=(),
    ),
    _notice(
        "e40ac396-cbac-43f4-8752-764ed60ccceb",  # Walt Disney World Railroad - Fantasyland
        "walt-disney-world-railroad-fantasyland",
        evidence=("transfer-to-wheelchair",),
        flags=(),
    ),
    _notice(
        "e39b831b-7731-49bb-815b-289b4f49a9fd",  # Walt Disney World Railroad - Main Street
        "walt-disney-world-railroad",
        evidence=("transfer-to-wheelchair",),
        flags=(),
    ),
    _notice(
        "8183f3f2-1b59-4b9c-b634-6a863bdf8d84",  # Walt Disney's Carousel of Progress
        "walt-disney-carousel-of-progress",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    _notice(
        "6fd1e225-53a0-4a80-a577-4bbc9a471075",  # Walt Disney's Enchanted Tiki Room
        "enchanted-tiki-room",
        evidence=("wheelchair-accessibility",),
        flags=(),
    ),
    # Shows and meet-and-greets (issue #69)
    _notice(
        "a0613b70-293f-4a5b-8169-357be1777c62",  # Casey's Corner Pianist
        "caseys-corner-pianist",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "f819079e-644e-4fce-bda3-26b899ac7027",  # Disney Adventure Friends Cavalcade
        "disney-adventure-friends-cavalcade",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "ee56b2f3-fd49-4a29-ae1a-2d321549a633",  # Disney Festival of Fantasy Parade
        "festival-fantasy-parade",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "d69261dc-62b8-434c-83bd-93649b43c408",  # Disney Starlight: Dream the Night Away
        "starlight-dream-night-away-parade",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "1c708beb-41e1-43ae-8dd8-1e85075aeb38",  # Flag Retreat
        "flag-retreat",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "22b78ed9-a692-47cb-b6a4-6d1224ff67e3",  # Happily Ever After
        "happily-ever-after-fireworks",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "51392ca4-f824-42d8-8808-8110ec8e0e22",  # Main Street Philharmonic
        "main-street-philharmonic",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "4c31b3ad-5dc9-437f-ac1a-0fdff36a2818",  # Mickey's Magical Friendship Faire
        "mickeys-magical-friendship-faire",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "1eee22e8-1d0a-4809-a42b-df3ae55c69d5",  # The Dapper Dans
        "dapper-dans",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "012a211b-4c91-451c-8a0e-5e3ab398eda8",  # Meet Ariel at Her Grotto
        "character-meet-ariel-grotto-fantasyland",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "40737d3d-0ff6-4a9e-a050-beb87bf90120",  # Meet Cinderella and a Visiting Princess
        "character-meet-princess-fantasyland",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "cf4b2ba4-3626-4de7-9d07-abe8a65b1665",  # Meet Princess Tiana and a Visiting Princess
        "character-meet-princess-fairytale-hall",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "166f2985-7b27-4eff-a8b3-29c3448ba198",  # Meet Daring Disney Pals (Goofy and Donald)
        "character-meet-goofy-donald",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "b5d6d1d1-e960-4c8f-a8a4-b9748b386b64",  # Meet Dashing Disney Pals (Minnie and Daisy)
        "character-meet-minnie-daisy",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
    _notice(
        "a2d92647-634d-4eb4-886b-9da858e871f1",  # Meet Mickey at Town Square Theater
        "character-meet-mickey-town-square",
        evidence=("wheelchair-accessibility",),
        flags=(),
        page=ENTERTAINMENT_PAGE,
        reviewed_on=date(2026, 10, 1),
    ),
)


def magic_kingdom_knowledge_store() -> InMemoryKnowledgeStore:
    """The in-memory KnowledgeStore over the reviewed Magic Kingdom corpus."""
    return InMemoryKnowledgeStore(
        {notice.attraction_id: notice.flags for notice in MAGIC_KINGDOM_SAFETY_NOTICES},
        corpus_version=NOTICE_CORPUS_VERSION,
    )
