"""Deterministic grounding of the times and the walking budget the extractor returns [G13].

Like a height, a departure time, a lunch window and a walking budget are hard
constraints: the planner will not trade them off. The model must not fill them
from nothing (a default "18:00", a "60 minutes" it made up), so each value has to
trace back to something the guests wrote, in English or Spanish, in digits or in
words. A value that matches nothing they typed is asked about, never stored.

The reading is deliberately generous about form ("6", "6pm", "18h", "a las seis",
"six thirty", "half past six", "1 hour 30", "una hora y media") and strict about
substance: the figure itself must be one the guests wrote.
"""

import re
from collections.abc import Sequence

_MINUTES_PER_DAY = 24 * 60
LUNCH_TOLERANCE_MINUTES = 60

_HOUR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
}  # fmt: skip
_HOUR_WORD = "|".join(sorted(_HOUR_WORDS, key=len, reverse=True))

_PREPOSITION = (
    r"(?:(?:at|around|about|by|until|till|before|after|from|between|hasta|"
    r"antes\s+de|despu[eé]s\s+de|desde|hacia|entre|tipo|past)(?:\s+las?)?"
    r"|(?:a|como\s+a|para|sobre)\s+las?)\s+"
)
_MARKER = (
    r"(?:a\.?\s?m\.?|p\.?\s?m\.?|o'?\s?clock|de\s+la\s+ma[ñn]ana|de\s+la\s+tarde|"
    r"de\s+la\s+noche|en\s+la\s+noche)(?![a-z])"
)
_NOT_DIGIT_BEFORE = r"(?<![\d:.,])"
_UNMARKED = rf"(?!\s*(?:{_MARKER}|h(?:rs?|s)?(?![a-z])))"

_CLOCK = re.compile(
    rf"{_NOT_DIGIT_BEFORE}(\d{{1,2}})\s*[:.h]\s*(\d{{2}})(?!\d)", re.IGNORECASE
)
_WITH_MARKER = re.compile(
    rf"{_NOT_DIGIT_BEFORE}(\d{{1,2}})\s*({_MARKER}|h(?:rs?|s)?(?![a-z]))", re.IGNORECASE
)
_AFTER_PREPOSITION = re.compile(
    rf"\b{_PREPOSITION}(\d{{1,2}})(?![\d:]|[.,]\d){_UNMARKED}", re.IGNORECASE
)
_RANGE = re.compile(
    r"(?<![\d:.,])(\d{1,2})(?::\d{2})?\s*(?:-|–|—|to|a|y|and|hasta|until)\s*"
    rf"(?:las?\s+)?(\d{{1,2}})(?![\d:]|[.,]\d){_UNMARKED}",
    re.IGNORECASE,
)
_WORD_AFTER_PREPOSITION = re.compile(
    rf"\b{_PREPOSITION}({_HOUR_WORD})\b{_UNMARKED}", re.IGNORECASE
)
_WORD_WITH_MARKER = re.compile(rf"\b({_HOUR_WORD})\s*({_MARKER})", re.IGNORECASE)
_NOON = re.compile(r"\b(?:noon|midday|mediod[ií]a|medio\s+d[ií]a)\b", re.IGNORECASE)
_MIDNIGHT = re.compile(r"\b(?:midnight|medianoche|media\s+noche)\b", re.IGNORECASE)
_HALF = re.compile(r"\b(?:half|media|thirty|treinta)\b", re.IGNORECASE)
_QUARTER = re.compile(
    r"\b(?:quarter|cuarto|fifteen|quince|forty[\s-]five|cuarenta\s+y\s+cinco)\b",
    re.IGNORECASE,
)


def _marker_kind(marker: str) -> str | None:
    squeezed = re.sub(r"[.\s']", "", marker.lower())
    if squeezed in {"pm", "delatarde", "delanoche", "enlanoche"}:
        return "pm"
    if squeezed in {"am", "delamañana", "delamanana"}:
        return "am"
    return None


def _hours(hour: int, marker: str | None) -> set[int]:
    """The 24h hours a stated ``hour`` can mean: "6" is 06 or 18, "6 pm" only 18."""
    if hour > 24:
        return set()
    hour %= 24
    if marker == "pm":
        return {hour % 12 + 12}
    if marker == "am":
        return {hour % 12}
    if hour == 0 or hour > 12:
        return {hour}
    return {hour % 12, hour % 12 + 12}


def clock_times_mentioned(texts: Sequence[str]) -> set[int]:
    """Minutes since midnight of every clock time the texts could be stating."""
    found: set[int] = set()
    for text in texts:
        extra = {0}
        if _HALF.search(text):
            extra.add(30)
        if _QUARTER.search(text):
            extra |= {15, 45}
        spans: list[tuple[set[int], set[int]]] = []  # (hours, minutes)
        spans += [
            (_hours(int(m.group(1)), None), {int(m.group(2))})
            for m in _CLOCK.finditer(text)
        ]
        spans += [
            (_hours(int(m.group(1)), _marker_kind(m.group(2))), extra)
            for m in _WITH_MARKER.finditer(text)
        ]
        spans += [
            (_hours(int(m.group(1)), None), extra)
            for m in _AFTER_PREPOSITION.finditer(text)
        ]
        spans += [
            (_hours(int(m.group(i)), None), extra)
            for m in _RANGE.finditer(text)
            for i in (1, 2)
        ]
        spans += [
            (_hours(_HOUR_WORDS[m.group(1).lower()], None), extra)
            for m in _WORD_AFTER_PREPOSITION.finditer(text)
        ]
        spans += [
            (_hours(_HOUR_WORDS[m.group(1).lower()], _marker_kind(m.group(2))), extra)
            for m in _WORD_WITH_MARKER.finditer(text)
        ]
        if _NOON.search(text):
            spans.append(({12}, {0}))
        if _MIDNIGHT.search(text):
            spans.append(({0}, {0}))
        for hours, minutes in spans:
            found.update(h * 60 + m for h in hours for m in minutes if 0 <= m < 60)
    return found


def _to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def departure_is_grounded(hhmm: str, texts: Sequence[str]) -> bool:
    return _to_minutes(hhmm) in clock_times_mentioned(texts)


def lunch_window_is_grounded(start: str, end: str, texts: Sequence[str]) -> bool:
    """Each end of the window lies within an hour of a time the guests wrote.

    "lunch around 1 PM" legitimately becomes 12:30-13:30, so the window is not
    compared to the stated time exactly, only kept near it.
    """
    stated = clock_times_mentioned(texts)
    return all(
        any(abs(_to_minutes(edge) - t) <= LUNCH_TOLERANCE_MINUTES for t in stated)
        for edge in (start, end)
    )


# -- walking budget ---------------------------------------------------------------------

_QUANTITY_WORDS = {
    "a": 1, "an": 1, "un": 1, "una": 1, "one": 1, "uno": 1, "two": 2, "dos": 2,
    "three": 3, "tres": 3, "four": 4, "cuatro": 4, "five": 5, "cinco": 5, "six": 6,
    "seis": 6, "seven": 7, "siete": 7, "eight": 8, "ocho": 8, "nine": 9, "nueve": 9,
    "ten": 10, "diez": 10, "eleven": 11, "once": 11, "twelve": 12, "doce": 12,
    "fifteen": 15, "quince": 15, "twenty": 20, "veinte": 20, "twenty-five": 25,
    "twenty five": 25, "veinticinco": 25, "thirty": 30, "treinta": 30,
    "thirty-five": 35, "thirty five": 35, "treinta y cinco": 35, "forty": 40,
    "cuarenta": 40, "forty-five": 45, "forty five": 45, "cuarenta y cinco": 45,
    "fifty": 50, "cincuenta": 50, "fifty-five": 55, "fifty five": 55,
    "cincuenta y cinco": 55, "sixty": 60, "sesenta": 60, "seventy": 70, "setenta": 70,
    "eighty": 80, "ochenta": 80, "ninety": 90, "noventa": 90,
}  # fmt: skip
_QUANTITY_WORD = "|".join(sorted(_QUANTITY_WORDS, key=len, reverse=True))
_MIN_UNIT = r"(?:minutes?|mins?|minutos?|mn)(?![a-z])"
_HOUR_UNIT = r"(?:hours?|hrs?|horas?|hs?)(?![a-z])"
_NUM = r"(\d+(?:[.,]\d+)?)"
_HALF_SUFFIX = r"\s*(?:and\s+(?:a\s+)?half|y\s+media)"

_DIGITS_MINUTES = re.compile(rf"{_NUM}\s*{_MIN_UNIT}", re.IGNORECASE)
_DIGITS_HOURS = re.compile(
    rf"{_NUM}\s*{_HOUR_UNIT}(?:({_HALF_SUFFIX})|\s*(?:and|y)?\s*(\d{{1,2}})\s*{_MIN_UNIT})?",
    re.IGNORECASE,
)
_WORDS_UNIT = re.compile(
    rf"\b({_QUANTITY_WORD})\s+({_MIN_UNIT}|{_HOUR_UNIT})({_HALF_SUFFIX})?",
    re.IGNORECASE,
)
_HOUR_AND_A_HALF = re.compile(
    r"\b(?:hour\s+and\s+(?:a\s+)?half|hora\s+y\s+media)\b", re.IGNORECASE
)
_HALF_AN_HOUR = re.compile(
    r"\b(?:half\s+(?:an?\s+)?hour|media\s+hora)\b", re.IGNORECASE
)
_QUARTER_HOUR = re.compile(
    r"\b(?:(?:a\s+)?quarter\s+(?:of\s+an?\s+)?hour|(?:un\s+)?cuarto\s+de\s+hora)\b",
    re.IGNORECASE,
)
_THREE_QUARTERS = re.compile(
    r"\b(?:three[\s-]quarters\s+of\s+an?\s+hour|tres\s+cuartos\s+de\s+hora)\b",
    re.IGNORECASE,
)


def _decimal(raw: str) -> float:
    return float(raw.replace(",", "."))


def walking_minutes_mentioned(texts: Sequence[str]) -> list[float]:
    """Every duration, in minutes, that the texts could be stating as a walking limit."""
    found: list[float] = []
    for text in texts:
        found.extend(_decimal(m.group(1)) for m in _DIGITS_MINUTES.finditer(text))
        for m in _DIGITS_HOURS.finditer(text):
            hours = _decimal(m.group(1)) * 60
            found.append(hours)
            if m.group(2):
                found.append(hours + 30)
            if m.group(3):
                found.append(hours + int(m.group(3)))
        for m in _WORDS_UNIT.finditer(text):
            quantity = _QUANTITY_WORDS[re.sub(r"\s+", " ", m.group(1).lower())]
            is_hours = re.match(_HOUR_UNIT, m.group(2), re.IGNORECASE) is not None
            total = quantity * (60 if is_hours else 1)
            found.append(total)
            if is_hours and m.group(3):
                found.append(total + 30)
        found += [90.0] * len(_HOUR_AND_A_HALF.findall(text))
        found += [30.0] * len(_HALF_AN_HOUR.findall(text))
        found += [15.0] * len(_QUARTER_HOUR.findall(text))
        found += [45.0] * len(_THREE_QUARTERS.findall(text))
    return found


def walking_budget_is_grounded(minutes: int, texts: Sequence[str]) -> bool:
    return any(
        abs(minutes - stated) < 0.5 for stated in walking_minutes_mentioned(texts)
    )
