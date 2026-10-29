"""A reply to "how many minutes can you walk?" is read without the LLM, in the usual forms."""

import pytest

from parkmind.agents.walking_answer import parse_walking_answer


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
        "800 m",  # a distance, not 800 minutes
        "2 km",
    ],
)
def test_a_reply_that_states_no_walking_limit_in_minutes_is_not_read(
    answer: str,
) -> None:
    assert parse_walking_answer(answer) is None
