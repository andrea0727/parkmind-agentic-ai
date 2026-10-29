"""Grounding of departure time, lunch window and walking budget in the guests' words [G13]."""

from datetime import date, datetime

import pytest
from elicit_support import FakeExtractor, make_intake, make_names
from langchain_core.messages import AIMessage, HumanMessage

from parkmind.agents.elicit_agent import make_elicit_node, ungrounded_times
from parkmind.agents.elicit_schema import ElicitationExtraction
from parkmind.agents.time_grounding import (
    departure_is_grounded,
    lunch_window_is_grounded,
    walking_budget_is_grounded,
)
from parkmind.core.contracts import PARK_TZ

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 9, 0, tzinfo=PARK_TZ)


def _extraction(
    departure: str | None = "18:00",
    lunch: tuple[str, str] | None = None,
    budget: int | None = None,
) -> ElicitationExtraction:
    return ElicitationExtraction.model_validate(
        {
            "guests": [{"role": "adult"}],
            "departure_time": departure,
            "lunch_window": {"start": lunch[0], "end": lunch[1]} if lunch else None,
            "party_walking_budget_minutes": budget,
        }
    )


@pytest.mark.parametrize(
    "text, departure",
    [
        ("We leave at 6 PM", "18:00"),
        ("we leave at 6pm", "18:00"),
        ("we leave at 6", "18:00"),
        ("we leave at 18:00", "18:00"),
        ("leaving at 6:30 pm", "18:30"),
        ("out by 18h", "18:00"),
        ("out by 18h30", "18:30"),
        ("we head out at six", "18:00"),
        ("we head out at six pm", "18:00"),
        ("we leave at half past six", "18:30"),
        ("we leave at noon", "12:00"),
        ("salimos a las 6", "18:00"),
        ("salimos a las 6 de la tarde", "18:00"),
        ("salimos a las seis", "18:00"),
        ("salimos a las seis y media", "18:30"),
        ("salimos a las 6:30", "18:30"),
        ("nos vamos al mediodía", "12:00"),
        ("salimos a las 8 de la noche", "20:00"),
        ("We leave the park at 8 PM.", "20:00"),
    ],
)
def test_a_departure_the_guests_wrote_is_grounded(text: str, departure: str) -> None:
    assert departure_is_grounded(departure, [text])


@pytest.mark.parametrize(
    "text, departure",
    [
        ("Two adults and two kids, we love thrill rides", "18:00"),
        ("We leave at 6 PM", "17:00"),
        ("We leave at 6 PM", "06:00"),
        ("We leave at 6 PM", "18:30"),
        ("salimos a las 8 de la noche", "08:00"),
        ("2 adults and 3 kids", "14:00"),
    ],
)
def test_a_departure_nobody_stated_is_not_grounded(text: str, departure: str) -> None:
    assert not departure_is_grounded(departure, [text])


def test_a_departure_from_an_earlier_message_still_counts() -> None:
    assert departure_is_grounded("20:00", ["we leave at 8 PM", "and we like thrills"])


@pytest.mark.parametrize(
    "text",
    [
        "lunch around 1 PM",
        "lunch around 1pm",
        "almorzamos como a la 1",
        "almorzar tipo 1 de la tarde",
        "lunch at 12:30",
    ],
)
def test_a_lunch_window_around_a_stated_time_is_grounded(text: str) -> None:
    assert lunch_window_is_grounded("12:30", "13:30", [text])


@pytest.mark.parametrize(
    "text",
    [
        "lunch between 12 and 2",
        "almuerzo entre las 12 y las 2",
        "lunch from 12 to 2 pm",
    ],
)
def test_a_stated_lunch_range_is_grounded(text: str) -> None:
    assert lunch_window_is_grounded("12:00", "14:00", [text])


@pytest.mark.parametrize(
    "text",
    ["we love thrill rides", "we leave at 6 PM", "dinner at 7 and leaving at 9"],
)
def test_a_lunch_window_nobody_stated_is_not_grounded(text: str) -> None:
    assert not lunch_window_is_grounded("12:30", "13:30", [text])


@pytest.mark.parametrize(
    "text, minutes",
    [
        ("walking at most 120 minutes", 120),
        ("keep walking under 2 hours", 120),
        ("keep the walking under three hours in total", 180),
        ("no more than 1.5 hours of walking", 90),
        ("an hour and a half of walking", 90),
        ("1 hour 30 minutes tops", 90),
        ("half an hour of walking", 30),
        ("45 mins of walking", 45),
        ("forty-five minutes", 45),
        ("caminar máximo 90 minutos", 90),
        ("caminar máximo dos horas", 120),
        ("caminar máximo una hora y media", 90),
        ("caminar media hora", 30),
        ("caminar máximo 1 hora", 60),
        ("an hour", 60),
        ("caminar 1h30 como mucho", 90),
        ("we can walk for 1 h 30", 90),
    ],
)
def test_a_walking_budget_the_guests_wrote_is_grounded(text: str, minutes: int) -> None:
    assert walking_budget_is_grounded(minutes, [text])


@pytest.mark.parametrize(
    "text, minutes",
    [
        ("we love thrill rides and leave at 6", 60),
        ("keep walking under 2 hours", 90),
        ("walk 30 minutes", 300),
        ("two adults and three kids", 120),
        ("2 horas 30 personas", 150),  # minutes after an hour only when no word follows
    ],
)
def test_a_walking_budget_nobody_stated_is_not_grounded(
    text: str, minutes: int
) -> None:
    assert not walking_budget_is_grounded(minutes, [text])


def test_ungrounded_times_names_each_value_that_matches_nothing() -> None:
    extraction = _extraction("18:00", ("12:30", "13:30"), 90)

    assert ungrounded_times(extraction, ["we love thrill rides"]) == [
        "departure_unverified",
        "lunch_window_unverified",
        "walking_budget_unverified",
    ]
    assert (
        ungrounded_times(
            extraction, ["leave at 6 PM, lunch around 1 PM, walk 90 minutes at most"]
        )
        == []
    )


def test_an_unstated_value_is_not_asked_when_it_was_not_extracted() -> None:
    assert ungrounded_times(_extraction(None), ["hello"]) == []


def _node(extraction: ElicitationExtraction):
    intake, _ = make_intake()
    extractor = FakeExtractor(extraction.model_dump(mode="json"))
    return make_elicit_node(
        extractor, intake, make_names(), today=lambda: TODAY, now=lambda: NOW
    )


def _state(*messages) -> dict:
    return {"thread_id": "t1", "messages": list(messages)}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Just me, an adult. Rides please!", "What time do you plan to leave"),
        (
            "Solo, adulto. ¡Quiero ir a todas las montañas rusas!",
            "¿A qué hora piensas salir",
        ),
    ],
)
def test_an_unstated_departure_is_asked_and_not_stored(
    text: str, expected: str
) -> None:
    update = _node(_extraction("18:00"))(_state(HumanMessage(content=text)))

    assert update["constraints"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == ["departure_unverified"]
    assert expected in message.content


def test_an_unstated_lunch_window_and_budget_are_asked_in_the_guests_language() -> None:
    node = _node(_extraction("18:00", ("12:30", "13:30"), 60))

    update = node(_state(HumanMessage(content="Somos dos adultos, salimos a las 6 pm")))

    assert update["constraints"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == [
        "lunch_window_unverified",
        "walking_budget_unverified",
    ]
    assert "almorzar" in message.content and "minutos" in message.content


def test_stated_values_pass_in_english_and_spanish() -> None:
    for text in (
        "I'm an adult. We leave at 6 PM, lunch around 1 PM, and can walk an hour in total.",
        "Soy adulto. Salimos a las 6 de la tarde, almorzamos como a la 1 y caminamos una hora en total.",
    ):
        update = _node(_extraction("18:00", ("12:30", "13:30"), 60))(
            _state(HumanMessage(content=text))
        )

        assert update["constraints"] is not None
        assert update["constraints"].party_walking_budget_minutes == 60


def test_a_bare_reply_to_the_departure_question_is_grounded() -> None:
    asked = AIMessage(
        content="?", additional_kwargs={"missing_information": ["departure_unverified"]}
    )
    node = _node(_extraction("18:00"))

    update = node(
        _state(
            HumanMessage(content="Just me, an adult"), asked, HumanMessage(content="18")
        )
    )

    assert update["constraints"] is not None
    assert update["constraints"].departure_time.hour == 18


def test_staying_until_closing_without_a_time_is_asked_not_assumed() -> None:
    """The extractor returned the park's closing hour; the guests never wrote a clock time."""
    update = _node(_extraction("22:00"))(
        _state(HumanMessage(content="Just me, an adult. We stay until closing."))
    )

    assert update["constraints"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == ["departure_unverified"]


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "Just me, an adult. Lunch 1-2 PM, leaving at 11 AM.",
            "Your lunch window (13:00-14:00) starts after the time you plan to leave (11:00)",
        ),
        (
            "Soy adulto. Almuerzo de 1 a 2 pm, salgo a las 11 am.",
            "Tu horario de almuerzo (13:00-14:00) empieza después de la hora en que piensas salir (11:00)",
        ),
    ],
)
def test_a_lunch_window_after_the_departure_is_asked_before_confirming(
    text: str, expected: str
) -> None:
    update = _node(_extraction("11:00", ("13:00", "14:00")))(
        _state(HumanMessage(content=text))
    )

    assert update["constraints"] is None
    assert update["pending_hard_constraint_confirmation"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == [
        "lunch_after_departure:13:00|14:00|11:00"
    ]
    assert expected in message.content


def test_a_lunch_window_before_the_departure_goes_to_confirmation() -> None:
    update = _node(_extraction("18:00", ("13:00", "14:00")))(
        _state(HumanMessage(content="I'm an adult. Lunch 1-2 PM, leaving at 6 PM."))
    )

    assert update["constraints"] is not None


def test_an_unverified_departure_is_asked_before_any_contradiction() -> None:
    update = _node(_extraction("11:00", ("13:00", "14:00")))(
        _state(HumanMessage(content="I'm an adult. Lunch 1-2 PM."))
    )

    assert update["messages"][0].additional_kwargs["missing_information"] == [
        "departure_unverified"
    ]
