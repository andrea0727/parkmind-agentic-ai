"""A short reply to "what time do you leave?" is read without the LLM, in the usual formats."""

import pytest
from conversation_support import MISSING, make_rig

from parkmind.agents.clock_answer import parse_clock
from parkmind.core.conversation import TurnStatus


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("18", "18:00"),
        ("18:00", "18:00"),
        ("18h", "18:00"),
        ("18hs", "18:00"),
        ("18.30", "18:30"),
        ("6 pm", "18:00"),
        ("6pm", "18:00"),
        ("6 p.m.", "18:00"),
        ("6:30 PM", "18:30"),
        ("6", "18:00"),
        ("a las 6", "18:00"),
        ("a las 6 de la tarde", "18:00"),
        ("9 de la noche", "21:00"),
        ("12 am", "00:00"),
        ("12", "12:00"),
        ("8 am", "08:00"),
        ("6 y media", "18:30"),
        ("we leave at 8 pm", "20:00"),
        ("salimos a las 7 pm", "19:00"),
        ("  18:00  ", "18:00"),
    ],
)
def test_common_ways_of_saying_a_time(answer, expected):
    assert parse_clock(answer) == expected


@pytest.mark.parametrize(
    "answer", ["", "no sé", "later", "3 adults", "25", "18:75", "between 5 and 6", "2 kids and 1 adult"]
)
def test_other_answers_are_left_to_the_extractor(answer):
    assert parse_clock(answer) is None


@pytest.mark.parametrize("answer", ["18", "18:00", "6 pm", "a las 6"])
def test_the_extractor_not_understanding_a_bare_time_does_not_repeat_the_question(answer):
    rig = make_rig(MISSING["extraction"])
    thread = rig.service.start()
    asked = rig.service.say(thread, " ".join(MISSING["messages"]))
    assert "departure_time" in asked.missing

    turn = rig.service.say(thread, answer)

    assert turn.status is TurnStatus.NEEDS_CONFIRMATION
    assert "departure_time" not in turn.missing


def test_an_answer_that_is_not_a_time_asks_again():
    rig = make_rig(MISSING["extraction"])
    thread = rig.service.start()
    rig.service.say(thread, " ".join(MISSING["messages"]))

    turn = rig.service.say(thread, "no sé todavía")

    assert turn.status is TurnStatus.NEEDS_ANSWER and "departure_time" in turn.missing
