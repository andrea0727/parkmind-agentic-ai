"""Reads the guests' short answer to "how many minutes can you walk?" without the LLM.

The extractor never sees the question, and a bare "30" carries no unit, so the
grounding check found no duration in it and the same question came back on every
turn [G13]. Like ``clock_answer`` for the departure, the reply to the walking
question is read here. The question asks for minutes, so a bare number is minutes;
"1h30" or "1:30" is ninety. The guests confirm the figure before it is used.
"""

import re
from dataclasses import dataclass

from parkmind.agents.time_grounding import QUANTITY_WORDS, walking_minutes_mentioned

MAX_MINUTES = 12 * 60
"""Longer than a park day: "18h30" is a clock time, never a walking limit."""
MAX_ABBREVIATED_MINUTES = 180
"""A number with a bare "m" ("30m") is chat shorthand for minutes up to here; "800 m" is a distance."""

_FILLER = frozenset(
    [
        "unos",
        "unas",
        "como",
        "más",
        "mas",
        "menos",
        "máximo",
        "maximo",
        "max",
        "hasta",
        "aprox",
        "aproximadamente",
        "alrededor",
        "de",
        "no",
        "en",
        "total",
        "podemos",
        "puede",
        "pueden",
        "caminar",
        "caminando",
        "creo",
        "que",
        "pues",
        "solo",
        "sólo",
        "about",
        "around",
        "roughly",
        "approximately",
        "up",
        "to",
        "at",
        "most",
        "maximum",
        "maybe",
        "in",
        "we",
        "i",
        "can",
        "walk",
        "walking",
        "just",
        "only",
        "than",
        "more",
        "less",
        "minutes",
        "minute",
        "mins",
        "min",
        "minutos",
        "minuto",
    ]
)
_NOT_A_QUANTITY = frozenset(["a", "an", "un", "una", "uno", "one"])
"""Number words too vague to be a walking limit on their own ("una" is not one minute)."""

_CLOCK_DURATION = re.compile(r"(?<![\d:])(\d{1,2}):([0-5]\d)(?![\d:])")
_MINUTES_ABBREVIATED = re.compile(r"(?<![\d.,])(\d{1,4})\s*m(?![a-z])", re.IGNORECASE)
_WORD = re.compile(r"[a-záéíóúñ]+|\d+", re.IGNORECASE)


@dataclass(frozen=True)
class WalkingAnswer:
    minutes: int


def _bare_minutes(answer: str) -> int | None:
    """The one number left once the filler words are gone: "unos 30", "treinta", "max 45"."""
    words = [w.lower() for w in _WORD.findall(answer)]
    rest = [w for w in words if w not in _FILLER]
    if len(rest) == 1 and rest[0].isdigit():
        return int(rest[0])
    phrase = " ".join(rest)
    if phrase and phrase not in _NOT_A_QUANTITY:
        return QUANTITY_WORDS.get(phrase)
    return None


def parse_walking_answer(answer: str) -> WalkingAnswer | None:
    """The walking limit the reply states, in minutes, or ``None`` when it states none."""
    stated = walking_minutes_mentioned([answer])
    # One phrase yields partial readings too ("1h30" is 60 and 90): the whole is the largest.
    minutes: float | None = max(stated) if stated else None
    if minutes is None and (clock := _CLOCK_DURATION.search(answer)):
        minutes = int(clock.group(1)) * 60 + int(clock.group(2))
    if minutes is None and (short := _MINUTES_ABBREVIATED.search(answer)):
        if int(short.group(1)) > MAX_ABBREVIATED_MINUTES:
            return None
        minutes = int(short.group(1))
    if minutes is None:
        minutes = _bare_minutes(answer)
    if minutes is None or not 0 < minutes <= MAX_MINUTES:
        return None
    return WalkingAnswer(int(minutes))
