"""Without a model the next question is still one topic, reacts to the guests and does not repeat itself."""

from parkmind.agents.ask_template import plain_question

FIXED = "¿A qué hora piensan salir del parque?"


def _ask(topic: str, turns, language="es", fixed: str = FIXED) -> str:
    return plain_question(topic, fixed, turns, language)


def test_a_greeting_is_greeted_back_and_one_topic_is_asked() -> None:
    out = _ask("guests", [("guest", "hola")])

    assert out.startswith("¡Hola!") and out.endswith(
        "¿Cuántas personas vienen al parque?"
    )


def test_an_answer_is_acknowledged_without_repeating_it() -> None:
    out = _ask("departure_time", [("guest", "somos 3 adultos")])

    assert out == f"Perfecto. {FIXED}"


def test_unsure_guests_get_three_options_and_a_different_set_each_time() -> None:
    first_turns = [("guest", "hola"), ("assistant", "x"), ("guest", "no sé")]
    first = _ask("guests", first_turns)
    second = _ask("guests", [*first_turns, ("assistant", first), ("guest", "no sé")])

    assert " o " in first and first != second
    assert " o " in second or "dos, tres o más" in second


def test_a_topic_asked_again_is_phrased_differently() -> None:
    first = _ask("departure_time", [("guest", "somos 3")])
    again = _ask(
        "departure_time",
        [
            ("guest", "somos 3"),
            ("assistant", first),
            ("guest", "llegamos por la tarde"),
        ],
    )

    assert "salir del parque" in first and "salir del parque" not in again


def test_english_has_its_own_wording() -> None:
    out = _ask("departure_time", [("guest", "not sure")], "en")

    assert out.startswith("No worries.") and "morning" in out


def test_other_topics_and_solo_guests_keep_the_fixed_question() -> None:
    turns = [("guest", "hola")]

    assert plain_question("unknown_attraction:x", "¿Cuál?", turns, "es") == "¿Cuál?"
    assert plain_question("departure_time", FIXED, turns, "es", solo=True) == FIXED
