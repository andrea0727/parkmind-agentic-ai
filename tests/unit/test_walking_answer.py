"""A reply to "how many minutes can you walk?" is read without the LLM, in the usual forms."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from parkmind.agents.walking_answer import (
    minutes_for_distance,
    parse_walking_answer,
    walking_distance_mentioned,
)
from parkmind.config.settings import settings
from parkmind.services.clients import routing_client

SRC = Path(__file__).resolve().parents[2] / "src"


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
        ("90m", 90),
        ("media hora", 30),
        ("half an hour", 30),  # not 60: "half an hour" also contains "an hour"
        ("an hour and a half", 90),
        ("three quarters of an hour", 45),
        ("dos horas y media", 150),
        ("entre 30 y 45 minutos", 30),  # a range is a limit up to its lower end
        ("30 to 45 minutes", 30),
        ("30-45 min", 30),
        ("5", 5),
        ("3 minutos", 3),  # a unit makes even a small figure the guests' own
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
        "2",  # too ambiguous bare: hours? people?
        "4",
        "mide 1,20 m",  # a height, not a walking limit
        "30 m",  # 30 meters is no day's walking limit
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
        ("150 m", 150, "150 m"),  # spaced: meters, even after "minutes, not distance"
        ("100m", 100, "100 m"),  # glued but above 90: meters
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


def test_only_a_distance_said_about_walking_is_a_walking_distance() -> None:
    assert walking_distance_mentioned(["Somos 2", "podemos caminar 2 km en total"])
    assert walking_distance_mentioned(["We can walk about 1 mile."])
    assert not walking_distance_mentioned(["Somos 2 adultos", "salimos a las 18"])
    assert not walking_distance_mentioned(["Ana mide 1,20 m"])  # a height
    assert not walking_distance_mentioned(["El hotel está a 800 m."])  # not walking


def test_a_walking_pace_of_zero_falls_back_to_the_minimum() -> None:
    """``PARKMIND_WALKING_SPEED_METERS_PER_MINUTE=0`` would divide by zero."""
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import dotenv; dotenv.load_dotenv = lambda *a, **k: False; "
                "from parkmind.config.settings import settings; "
                "print(settings.WALKING_SPEED_METERS_PER_MINUTE)"
            ),
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(SRC),
            "PARKMIND_WALKING_SPEED_METERS_PER_MINUTE": "0",
        },
        check=True,
    )

    assert float(out.stdout) == 1.0
