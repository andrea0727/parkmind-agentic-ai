"""A reply to "how many minutes can you walk?" is read without the LLM, in the usual forms."""

import pytest

from parkmind.agents.walking_answer import (
    minutes_for_distance,
    parse_walking_answer,
    walking_distance_mentioned,
)
from parkmind.config.settings import settings
from parkmind.services.clients import routing_client


@pytest.mark.parametrize(
    ("answer", "minutes"),
    [
        ("30", 30),
        ("unos 30", 30),
        ("máximo 45", 45),
        ("no más de 40", 40),
        ("podemos caminar 30", 30),
        ("about 30", 30),
        ("up to 45 minutes", 45),
        ("treinta", 30),
        ("cuarenta y cinco", 45),
        ("30 min", 30),
        ("30 minutos", 30),
        ("30m", 30),
        ("media hora", 30),
        ("1 hora", 60),
        ("una hora", 60),
        ("hora y media", 90),
        ("we can walk an hour and a half", 90),
        ("1h30", 90),
        ("1 h 30", 90),
        ("1:30", 90),
        ("2 horas y 30 minutos", 150),
    ],
)
def test_a_stated_walking_limit_is_read_in_minutes(answer: str, minutes: int) -> None:
    reply = parse_walking_answer(answer)

    assert reply is not None and reply.minutes == minutes


@pytest.mark.parametrize(
    "answer",
    [
        "no sé",
        "sí",
        "una",  # too vague to be one minute
        "1.5",  # a decimal with no unit says nothing about its unit
        "0",
        "18h30",  # a clock time, longer than a park day
    ],
)
def test_a_reply_that_states_no_walking_limit_is_not_read(answer: str) -> None:
    assert parse_walking_answer(answer) is None


@pytest.mark.parametrize(
    ("answer", "meters", "shown"),
    [
        ("2 km", 2000, "2 km"),
        ("2km", 2000, "2 km"),
        ("unos 2 kilómetros", 2000, "2 km"),
        ("dos kilómetros", 2000, "2 km"),
        ("1,5 km", 1500, "1,5 km"),
        ("800 m", 800, "800 m"),  # a distance, never 800 minutes
        ("800 metros", 800, "800 m"),
        ("1 mile", 1609.344, "1 mi"),
        ("podemos caminar como 2 km en total", 2000, "2 km"),
    ],
)
def test_a_distance_is_read_as_a_distance(
    answer: str, meters: float, shown: str
) -> None:
    reply = parse_walking_answer(answer)

    assert reply is not None and reply.minutes is None
    assert reply.meters == pytest.approx(meters) and reply.distance == shown


def test_minutes_win_when_the_reply_gives_both() -> None:
    reply = parse_walking_answer("2 km, o sea 30 minutos")

    assert reply is not None and (reply.minutes, reply.meters) == (30, None)


def test_a_distance_becomes_minutes_at_the_routing_pace_rounded_down() -> None:
    assert settings.WALKING_SPEED_METERS_PER_MINUTE == 75
    assert routing_client.DEFAULT_WALKING_SPEED_METERS_PER_MINUTE == 75
    assert minutes_for_distance(2000) == 26  # 26.7: the stricter limit
    assert minutes_for_distance(10) == 1


def test_a_distance_anywhere_in_the_guests_words_is_noticed() -> None:
    assert walking_distance_mentioned(["Somos 2", "podemos caminar 2 km en total"])
    assert not walking_distance_mentioned(["Somos 2 adultos", "salimos a las 18"])
