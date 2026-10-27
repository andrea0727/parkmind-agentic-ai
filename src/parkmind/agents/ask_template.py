"""The next question worded without a model: one topic, a short reaction, options for unsure guests, and a new phrasing each time the topic comes back."""

import re
from collections.abc import Sequence

from parkmind.agents.ask_agent import UNSURE, Turn
from parkmind.core.language import Language

GREETING = re.compile(
    r"^\W*(hola|buenas|buenos d[ií]as|buenas tardes|buenas noches|hi|hello|hey)\W*$",
    re.IGNORECASE,
)

REACTIONS: dict[str, dict[Language, list[str]]] = {
    "greeting": {
        "es": ["¡Hola! Con gusto les ayudo a planear su día en el parque."],
        "en": ["Hi! Happy to help you plan your day at the park."],
    },
    "unsure": {
        "es": ["No hay problema.", "Está bien."],
        "en": ["No worries.", "That's fine."],
    },
    "answer": {
        "es": ["Perfecto.", "Genial.", "Entendido."],
        "en": ["Great.", "Got it.", "Thanks."],
    },
}

Phrasings = dict[Language, list[str]]
QUESTIONS: dict[str, dict[str, Phrasings]] = {
    "guests": {
        "ask": {
            "es": ["¿Cuántas personas vienen al parque?", "¿Cuántos son en total?"],
            "en": [
                "How many people are coming to the park?",
                "How many of you are there in total?",
            ],
        },
        "unsure": {
            "es": [
                "¿Vienen solos, en pareja o en familia?",
                "¿Son dos, tres o más personas?",
                "¿Vienen solo adultos o también niños?",
            ],
            "en": [
                "Are you coming alone, as a couple or as a family?",
                "Is it two, three or more people?",
                "Is it only adults, or are there children too?",
            ],
        },
    },
    "departure_time": {
        "ask": {
            "es": [
                "¿A qué hora piensan salir del parque?",
                "¿Hasta qué hora les gustaría quedarse?",
                "¿A qué hora quieren terminar la visita?",
            ],
            "en": [
                "What time do you plan to leave the park?",
                "Until what time would you like to stay?",
                "When would you like to finish the visit?",
            ],
        },
        "unsure": {
            "es": [
                "¿Prefieren salir por la mañana, por la tarde o al cierre del parque?",
                "¿Se irían antes de comer, después de comer o al cierre?",
                "¿Salen temprano o se quedan hasta el final?",
            ],
            "en": [
                "Would you rather leave in the morning, in the afternoon or at closing?",
                "Would you go before lunch, after lunch or at closing?",
                "Do you leave early or stay until the end?",
            ],
        },
    },
}


def _times_asked(phrasings: Sequence[str], turns: Sequence[Turn]) -> int:
    return sum(
        1
        for role, said in turns
        if role == "assistant" and any(p in said for p in phrasings)
    )


def _reaction(said: str, unsure: bool, language: Language, assistant_turns: int) -> str:
    kind = "greeting" if GREETING.match(said) else "unsure" if unsure else "answer"
    options = REACTIONS[kind][language]
    return options[assistant_turns % len(options)]


def plain_question(
    topic: str | None,
    fixed: str,
    turns: Sequence[Turn],
    language: Language,
    *,
    solo: bool = False,
) -> str:
    """``fixed`` made conversational, or untouched when the topic has no phrasings or the guest is alone (the tú form lives in ``fixed``)."""
    phrasings = QUESTIONS.get(topic or "")
    said = next((text for role, text in reversed(turns) if role == "guest"), None)
    if phrasings is None or solo or said is None:
        return fixed
    unsure = bool(UNSURE.search(said))
    kind = "unsure" if unsure else "ask"
    options = phrasings[kind][language]
    question = options[_times_asked(options, turns) % len(options)]
    assistant_turns = sum(1 for role, _ in turns if role == "assistant")
    return f"{_reaction(said, unsure, language, assistant_turns)} {question}"
