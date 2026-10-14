import re
import string
from datetime import date, datetime

import pytest
from elicit_support import FakeExtractor, make_intake, make_names, scenario
from langchain_core.messages import HumanMessage

from parkmind.agents.elicit_agent import make_elicit_node
from parkmind.core.contracts import PARK_TZ
from parkmind.core.language import _CATALOG, as_language, detect_language, text
from parkmind.services.use_cases.confirmation_echo import echo_for
from parkmind.services.use_cases.replan_reason import replan_reason


@pytest.mark.parametrize(
    "message",
    [
        "Somos 2 adultos y un niño de 6 años, queremos salir a las 6 de la tarde",
        "Hola, quiero evitar las montañas rusas",
        "mi hijo mide 1.05 metros",
        "¿Cuál es el plan?",
    ],
)
def test_spanish_is_detected(message: str) -> None:
    assert detect_language(message) == "es"


@pytest.mark.parametrize(
    "message",
    [
        "We are 2 adults and a 6 year old child, we want to leave at 6 pm",
        "Hello, I would like to avoid the roller coasters",
        "my son is tall but he is tired",
    ],
)
def test_english_is_detected(message: str) -> None:
    assert detect_language(message, fallback="es") == "en"


def test_a_message_with_no_signal_keeps_the_conversations_language() -> None:
    assert detect_language("1.05 m", fallback="es") == "es"
    assert detect_language("ok", fallback="en") == "en"


def test_the_language_follows_the_latest_message() -> None:
    assert detect_language("we want to avoid the water rides", fallback="es") == "en"


def test_as_language_accepts_only_supported_values() -> None:
    assert as_language("es") == "es"
    assert as_language("fr") == "en"
    assert as_language(None, fallback="es") == "es"


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_catalog_entry_exists_in_both_languages_with_compatible_placeholders() -> (
    None
):
    for key, by_lang in _CATALOG.items():
        assert set(by_lang) == {"en", "es"}, key
        assert _fields(by_lang["es"]) <= _fields(by_lang["en"]), key
        assert by_lang["es"].strip() or key == "reason_and", key


def test_spanish_wording_is_not_the_english_wording() -> None:
    identical = {"diff_line", "event_behind_by"}
    for key, by_lang in _CATALOG.items():
        if key not in identical:
            assert by_lang["en"] != by_lang["es"], key


def test_echoes_are_in_spanish() -> None:
    assert echo_for("height:g2: 122", "es").startswith("La Persona 2 mide 122 cm")
    assert "imprescindible" in echo_for("must_do: Space Mountain", "es")
    assert echo_for("must_do: Space Mountain").startswith(
        "'Space Mountain' is a must-do"
    )


def test_replan_reason_in_spanish() -> None:
    assert (
        replan_reason([], {}, "es").text
        == "El plan se actualizó tras un cambio en el parque."
    )


def test_text_formats_its_parameters() -> None:
    assert text("q_guest_role", "es", who="Persona 2") == "¿Persona 2 es adulto o niño?"


def _node_and_state(message: str, language: str | None = None):
    case = scenario("wiki_example_missing_departure")
    intake, _ = make_intake()
    today = date(2026, 10, 6)
    node = make_elicit_node(
        FakeExtractor(case["extraction"]),
        intake,
        make_names(),
        today=lambda: today,
        now=lambda: datetime(2026, 10, 6, 9, 0, tzinfo=PARK_TZ),
    )
    state: dict = {"thread_id": "t1", "messages": [HumanMessage(content=message)]}
    if language:
        state["language"] = language
    return node, state


def test_the_elicit_node_answers_in_the_language_the_guest_wrote() -> None:
    node, state = _node_and_state("Somos dos adultos y un niño, queremos ir al parque")
    update = node(state)

    assert update["language"] == "es"
    assert "salir" in update["messages"][0].content


def test_the_elicit_node_keeps_the_state_language_for_an_ambiguous_message() -> None:
    node, state = _node_and_state("1.05 m", language="es")

    assert node(state)["language"] == "es"


def test_the_elicit_node_defaults_to_english() -> None:
    node, state = _node_and_state("We are two adults and a child")
    update = node(state)

    assert update["language"] == "en"
    assert re.search(r"leave", update["messages"][0].content)


def test_guest_role_question_never_shows_the_internal_id() -> None:
    from parkmind.agents.elicit_agent import _question_for

    assert _question_for(["guest_role:g1"], None, "es", 1) == "¿Eres adulto o niño?"
    assert _question_for(["guest_role:g2"], None, "es", 2) == "¿La Persona 2 es adulto o niño?"
    assert _question_for(["guest_role:g2"], None, "en", 2) == "Is Guest 2 an adult or a child?"
    assert _question_for(["guest_role:g2"], None, "es", 2, [None, "tu mamá"]) == (
        "¿Tu mamá es adulto o niño?"
    )
    assert _question_for(["guest_role:g2"], None, "en", 2, [None, "your mom"]) == (
        "Is your mom an adult or a child?"
    )


def test_unknown_attraction_question_lists_what_the_park_has_by_intensity() -> None:
    from parkmind.agents.elicit_agent import _question_for
    from parkmind.services.use_cases.resolve_attraction_names import NameResolution

    resolution = NameResolution(
        unknown=("Space Rocket",),
        options_by_category={"THRILL": ("Big Drop", "Twister"), "FAMILY": ("Carousel",)},
    )
    missing = ["unknown_attraction:Space Rocket"]

    solo = _question_for(missing, resolution, "es", 1)
    group = _question_for(missing, resolution, "es", 3)

    assert "intensas (Big Drop, Twister)" in solo
    assert "suaves o familiares (Carousel)" in solo
    assert solo.endswith("¿A cuál te refieres?")
    assert group.endswith("¿A cuál se refieren?")


def test_every_solo_wording_has_the_same_placeholders_as_the_plural_one() -> None:
    fields = string.Formatter().parse
    for key, entry in _CATALOG.items():
        if key.endswith("_solo") and key.removesuffix("_solo") in _CATALOG:
            base = _CATALOG[key.removesuffix("_solo")]
            for lang in entry:
                solo_fields = {f for _, f, _, _ in fields(entry[lang]) if f}
                base_fields = {f for _, f, _, _ in fields(base[lang]) if f}
                assert solo_fields <= base_fields, key


def test_party_of_one_is_addressed_in_the_singular() -> None:
    assert text("keeps_plan", "es", solo=True) == "Tu plan actual sigue como estaba."
    assert text("keeps_plan", "es") == "Su plan actual sigue como estaba."
    assert echo_for("departure_time: 18:00", "es", solo=True).startswith("Sales ")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Voy solo, mido 1.75, salgo a las 8pm", 175.0),
        ("Voy solo, mido 1,75, salgo a las 8pm", 175.0),
        ("I'm 175, leaving at 8pm", 175.0),
        ("Mido 1.75.", 175.0),
    ],
)
def test_a_height_followed_by_punctuation_is_still_found(message: str, expected: float) -> None:
    from parkmind.agents.elicit_agent import heights_mentioned_cm

    assert expected in heights_mentioned_cm(message)


def test_a_time_is_not_read_as_a_height() -> None:
    from parkmind.agents.elicit_agent import heights_mentioned_cm

    assert 18.0 not in heights_mentioned_cm("salimos a las 18:30, somos 2")
