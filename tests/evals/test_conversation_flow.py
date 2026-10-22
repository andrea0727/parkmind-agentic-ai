"""The flow metric fails a repeated question and a parroted sentence, and passes a natural exchange."""

import pytest

pytest.importorskip("deepeval")

from evals.eval_conversation import (
    ConversationFlowMetric,
    conversation_test_case,
    flow_problems,
)

ASK = "¿A qué hora piensan salir del parque?"

NATURAL = [
    ("guest", "hola"),
    ("assistant", "¡Hola! ¿Cuántas personas vienen y a qué hora piensan salir?"),
    ("guest", "no sé"),
    ("assistant", "Tranquilo. ¿Salen por la mañana, por la tarde o al cierre?"),
    ("guest", "somos 2 adultos y un niño de 6 años"),
    ("assistant", "Perfecto, gracias. ¿Y a qué hora piensan salir?"),
]


def score(turns) -> ConversationFlowMetric:
    metric = ConversationFlowMetric()
    metric.measure(conversation_test_case(turns))
    return metric


def test_a_natural_exchange_passes() -> None:
    assert score(NATURAL).is_successful()


def test_the_same_question_asked_twice_fails_even_when_case_and_punctuation_differ() -> (
    None
):
    turns = [
        ("guest", "hola"),
        ("assistant", ASK),
        ("guest", "no sé"),
        ("assistant", "a qué hora piensan salir del parque"),
    ]

    metric = score(turns)

    assert not metric.is_successful()
    assert "turn 4 repeats an earlier assistant turn" in str(metric.reason)


def test_reading_the_guests_sentence_back_fails() -> None:
    turns = [
        ("guest", "somos 2 adultos y un niño de 6 años"),
        ("assistant", f"Perfecto, somos 2 adultos y un niño de 6 años. {ASK}"),
    ]

    metric = score(turns)

    assert not metric.is_successful()
    assert "repeats back what the guests wrote" in str(metric.reason)


def test_a_short_answer_cannot_be_echoed() -> None:
    assert (
        flow_problems([("guest", "no sé"), ("assistant", "No sé qué decir. " + ASK)])
        == []
    )
