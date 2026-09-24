"""Reference coordinates and topology for theme park routing.

Provides curated geographic coordinates (latitude, longitude) for Magic Kingdom
attractions and landmarks to calculate realistic walking times when external routing
engines (OSRM/GraphHopper) are offline or during in-memory deterministic planning.
"""

MAGIC_KINGDOM_NODE_COORDINATES: dict[str, tuple[float, float]] = {
    # Main Street, U.S.A.
    "e39b831b-7731-49bb-815b-289b4f49a9fd": (
        28.4162,
        -81.5812,
    ),  # WDW Railroad - Main Street
    "888fb4a4-7adf-47a1-8ba2-c258cc64fd75": (28.4170, -81.5812),  # Main Street Vehicles
    # Adventureland
    "de737ffc-306b-4f32-8bbb-34e5d370ec8f": (28.4182, -81.5845),  # A Pirate's Adventure
    "30fe3c64-af71-4c66-a54b-aa61fd7af177": (
        28.4180,
        -81.5835,
    ),  # Swiss Family Treehouse
    "96455de6-f4f1-403c-9391-bf8396979149": (
        28.4185,
        -81.5842,
    ),  # The Magic Carpets of Aladdin
    "6fd1e225-53a0-4a80-a577-4bbc9a471075": (
        28.4183,
        -81.5838,
    ),  # Walt Disney's Enchanted Tiki Room
    "352feb94-e52e-45eb-9c92-e4b44c6b1a9d": (
        28.4186,
        -81.5856,
    ),  # Pirates of the Caribbean
    "796b0a25-c51e-456e-9bb8-50a324e301b3": (28.4181, -81.5840),  # Jungle Cruise
    # Frontierland
    "de3309ca-97d5-4211-bffe-739fed47e92f": (
        28.4202,
        -81.5858,
    ),  # Big Thunder Mountain Railroad
    "0f57cecf-5502-4503-8bc3-ba84d3708ace": (
        28.4188,
        -81.5847,
    ),  # Country Bear Musical Jamboree
    "73cb9445-0695-47a3-87ce-d08ae36b5f3c": (
        28.4206,
        -81.5855,
    ),  # Tiana's Bayou Adventure
    # Liberty Square
    "2551a77d-023f-4ab1-9a19-8afec0190f39": (28.4204, -81.5835),  # Haunted Mansion
    "2ebfb38c-5cb5-4de1-86c0-f7af14188022": (
        28.4198,
        -81.5828,
    ),  # The Hall of Presidents
    # Fantasyland
    "924a3b2c-6b4b-49e5-99d3-e9dc3f2e8a48": (28.4215, -81.5785),  # The Barnstormer
    "e40ac396-cbac-43f4-8752-764ed60ccceb": (
        28.4220,
        -81.5788,
    ),  # WDW Railroad - Fantasyland
    "9d4d5229-7142-44b6-b4fb-528920969a2c": (
        28.4207,
        -81.5802,
    ),  # Seven Dwarfs Mine Train
    "e76c93df-31af-49a5-8e2f-752c76c937c9": (
        28.4212,
        -81.5808,
    ),  # Enchanted Tales with Belle
    "f5aad2d4-a419-4384-bd9a-42f86385c750": (28.4205, -81.5822),  # "it's a small world"
    "890fa430-89c0-4a3f-96c9-11597888005e": (
        28.4212,
        -81.5792,
    ),  # Dumbo the Flying Elephant
    "86a41273-5f15-4b54-93b6-829f140e5161": (28.4202, -81.5818),  # Peter Pan's Flight
    "f010bc01-b450-4476-a5f3-a5f2813104b2": (
        28.4218,
        -81.5786,
    ),  # Casey Jr. Splash 'N' Soak Station
    "273ddb8d-e7b5-4e34-8657-1113f49262a5": (
        28.4203,
        -81.5812,
    ),  # Prince Charming Regal Carrousel
    "0aae716c-af13-4439-b638-d75fb1649df3": (28.4204, -81.5798),  # Mad Tea Party
    "7c5e1e02-3a44-4151-9005-44066d5ba1da": (
        28.4201,
        -81.5815,
    ),  # Mickey's PhilharMagic
    "0d94ad60-72f0-4551-83a6-ebaecdd89737": (
        28.4206,
        -81.5799,
    ),  # The Many Adventures of Winnie the Pooh
    "3cba0cb4-e2a6-402c-93ee-c11ffcb127ef": (
        28.4218,
        -81.5802,
    ),  # Under the Sea - Journey of The Little Mermaid
    "90d79335-c907-4069-a021-d0fe1ec73ae2": (
        28.4194,
        -81.5812,
    ),  # Cinderella Castle (Hub)
    # Tomorrowland
    "f163ddcd-43e1-488d-8276-2381c1db0a39": (
        28.4198,
        -81.5780,
    ),  # Tomorrowland Speedway
    "b2260923-9315-40fd-9c6b-44dd811dbe64": (28.4192, -81.5772),  # Space Mountain
    "8183f3f2-1b59-4b9c-b634-6a863bdf8d84": (
        28.4180,
        -81.5785,
    ),  # Walt Disney's Carousel of Progress
    "ffcfeaa2-1416-4920-a1ed-543c1a1695c4": (
        28.4189,
        -81.5786,
    ),  # Tomorrowland Transit Authority PeopleMover
    "e8f0b426-7645-4ea3-8b41-b94ae7091a41": (
        28.4184,
        -81.5796,
    ),  # Monsters, Inc. Laugh Floor
    "d9d12438-d999-4482-894b-8955fdb20ccf": (28.4189, -81.5786),  # Astro Orbiter
    "5a43d1a7-ad53-4d25-abfe-25625f0da304": (
        28.4201,
        -81.5762,
    ),  # TRON Lightcycle / Run
    "72c7343a-f7fb-4f66-95df-c91016de7338": (
        28.4187,
        -81.5792,
    ),  # Buzz Lightyear's Space Ranger Spin
}

# Curated direct walking times (in minutes) for key frequently walked paths
MAGIC_KINGDOM_DIRECT_WALKING_TIMES: dict[tuple[str, str], float] = {
    # Hub <-> Major Land Entry Points
    (
        "90d79335-c907-4069-a021-d0fe1ec73ae2",
        "b2260923-9315-40fd-9c6b-44dd811dbe64",
    ): 5.0,  # Hub <-> Space Mountain
    (
        "90d79335-c907-4069-a021-d0fe1ec73ae2",
        "de3309ca-97d5-4211-bffe-739fed47e92f",
    ): 6.0,  # Hub <-> Big Thunder
    (
        "90d79335-c907-4069-a021-d0fe1ec73ae2",
        "352feb94-e52e-45eb-9c92-e4b44c6b1a9d",
    ): 4.5,  # Hub <-> Pirates
    (
        "90d79335-c907-4069-a021-d0fe1ec73ae2",
        "2551a77d-023f-4ab1-9a19-8afec0190f39",
    ): 3.5,  # Hub <-> Haunted Mansion
    (
        "90d79335-c907-4069-a021-d0fe1ec73ae2",
        "9d4d5229-7142-44b6-b4fb-528920969a2c",
    ): 3.0,  # Hub <-> Seven Dwarfs
    # Tomorrowland Intra-land
    (
        "b2260923-9315-40fd-9c6b-44dd811dbe64",
        "5a43d1a7-ad53-4d25-abfe-25625f0da304",
    ): 2.5,  # Space Mtn <-> TRON
    (
        "b2260923-9315-40fd-9c6b-44dd811dbe64",
        "72c7343a-f7fb-4f66-95df-c91016de7338",
    ): 2.0,  # Space Mtn <-> Buzz
    # Frontierland Intra-land
    (
        "de3309ca-97d5-4211-bffe-739fed47e92f",
        "73cb9445-0695-47a3-87ce-d08ae36b5f3c",
    ): 1.5,  # Big Thunder <-> Tiana
}
