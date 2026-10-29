"""Reads the guests' short answer to "how many minutes can you walk?" without the LLM.

The extractor never sees the question, and a bare "30" carries no unit, so the
grounding check found no duration in it and the same question came back on every
turn [G13]. Like ``clock_answer`` for the departure, the reply to the walking
question is read here. The question asks for minutes, so a bare number is minutes;
"1h30" or "1:30" is ninety. The guests confirm the figure before it is used.

A distance ("2 km") is read as a distance. The plan counts walking in minutes, so the
guests are first asked again in minutes; a second distance is converted at the pace
the routing client walks (``WALKING_SPEED_METERS_PER_MINUTE``), and the confirmation
says it was converted.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from parkmind.agents.time_grounding import (
    MINUTE_UNIT,
    QUANTITY_WORDS,
    spell_out_fractions,
    walking_minutes_mentioned,
)
from parkmind.config.settings import settings

MAX_MINUTES = 12 * 60
"""Longer than a park day: "18h30" is a clock time, never a walking limit."""
MAX_ABBREVIATED_MINUTES = 90
"""A number glued to "m" ("30m") is chat shorthand for minutes up to here; "100m" is a distance."""
MIN_BARE_MINUTES = 5
"""A bare "2" is too ambiguous (hours? people?) to be taken as two minutes."""
MIN_DISTANCE_METERS = 100
"""Shorter is a height ("1,20 m") or a typo, never a day's walking limit."""

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
_MINUTES_ABBREVIATED = re.compile(r"(?<![\d.,])(\d{1,4})m(?![a-z])", re.IGNORECASE)
"""Only glued ("30m"): a spaced "150 m" is a distance."""
_RANGE = re.compile(
    rf"(?<![\d.,])(\d+)\s*(?:-|–|a|y|to|and|o|or)\s*(\d+)\s*{MINUTE_UNIT}",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-záéíóúñ]+|\d+", re.IGNORECASE)
_WALKING_WORD = re.compile(r"\b(?:camin\w*|andar|recorr\w*|walk\w*)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"[^.;!?\n]+")

_METERS_PER_UNIT = {"km": 1000.0, "mi": 1609.344, "m": 1.0}
_UNIT = {
    "km": r"km|kms|kil[oó]metros?|kilomet(?:er|re)s?",
    "mi": r"mi|miles?|millas?",
    "m": r"m|mts?|metros?|met(?:er|re)s?",
}
_QUANTITY_WORD = "|".join(sorted(QUANTITY_WORDS, key=len, reverse=True))
_DISTANCE = re.compile(
    rf"(?<![\d.,])(\d+(?:[.,]\d+)?|{_QUANTITY_WORD})\s*"
    rf"(?:(?P<km>{_UNIT['km']})|(?P<mi>{_UNIT['mi']})|(?P<m>{_UNIT['m']}))"
    r"(?![a-záéíóúñ])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WalkingAnswer:
    minutes: int | None = None
    """The walking limit in minutes: stated, or converted from ``distance``."""
    meters: float | None = None
    distance: str | None = None
    """The distance as the guests gave it ("2 km"), when they gave one."""


def minutes_for_distance(meters: float) -> int:
    """Minutes of walking at the routing pace, rounded down: the stricter limit."""
    return max(1, math.floor(meters / settings.WALKING_SPEED_METERS_PER_MINUTE))


def _distance(text: str) -> WalkingAnswer | None:
    found = _DISTANCE.search(text)
    if found is None:
        return None
    raw = found.group(1)
    number = (
        float(raw.replace(",", "."))
        if raw[0].isdigit()
        else float(QUANTITY_WORDS[raw.lower()])
    )
    unit = next(u for u in ("km", "mi", "m") if found.group(u))
    meters = number * _METERS_PER_UNIT[unit]
    if meters < MIN_DISTANCE_METERS:
        return None
    shown = raw if raw[0].isdigit() else f"{number:g}"
    return WalkingAnswer(meters=meters, distance=f"{shown} {unit}")


def walking_distance_mentioned(texts: Sequence[str]) -> bool:
    """Whether the guests gave their walking limit as a distance.

    Only a sentence about walking counts: "Ana mide 1,20 m" or "el hotel está a 800 m"
    is not a walking limit.
    """
    return any(
        _WALKING_WORD.search(sentence) and _distance(sentence) is not None
        for text in texts
        for sentence in _SENTENCE.findall(text)
    )


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


def _minutes(answer: str) -> float | None:
    """The minutes a reply states with a unit or as a clock duration, else ``None``."""
    if span := _RANGE.search(answer):
        # A range is a limit only up to its lower end: the stricter reading, like a distance.
        return min(int(span.group(1)), int(span.group(2)))
    stated = walking_minutes_mentioned([answer])
    if stated:
        # One phrase yields partial readings too ("1h30" is 60 and 90): the whole is the largest.
        return max(stated)
    if clock := _CLOCK_DURATION.search(answer):
        return int(clock.group(1)) * 60 + int(clock.group(2))
    return None


def parse_walking_answer(answer: str) -> WalkingAnswer | None:
    """The walking limit the reply states: minutes, else a distance, else ``None``.

    Minutes win when the reply gives both ("2 km, o sea 30 minutos"): the plan counts them.
    """
    answer = spell_out_fractions(answer)
    minutes = _minutes(answer)
    if minutes is None and (short := _MINUTES_ABBREVIATED.search(answer)):
        if int(short.group(1)) > MAX_ABBREVIATED_MINUTES:
            return _distance(answer)
        minutes = int(short.group(1))
        if minutes < MIN_BARE_MINUTES:
            return None
    if minutes is None and (distance := _distance(answer)):
        return distance
    if minutes is None:
        minutes = _bare_minutes(answer)
        if minutes is not None and minutes < MIN_BARE_MINUTES:
            return None
    if minutes is None or not 0 < minutes <= MAX_MINUTES:
        return None
    return WalkingAnswer(minutes=int(minutes))
