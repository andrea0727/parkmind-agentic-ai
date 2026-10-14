"""Deterministic numeral check for explanations (section 40 [C17], P0-31).

"Every numeral in the explanation is extracted and asserted to be a subset of the
numerals in the supplied payload." No LLM judge sits on this path.

A numeral is a clock time (``14:05``, ``9:05``) or a number (``25``, ``0.8``),
including digits written against letters (``15min``). Both sides are normalized the
same way -- ``25`` and ``25.0`` are one numeral, ``09:05`` and ``9:05`` too -- and
nothing else is allowed: a bare ``14`` is not grounded by ``14:05``, and ``7`` is
not grounded by ``7.4``. Numbers written in English or Spanish words count by their
value (``forty`` and ``cuarenta`` are ``40``, ``twenty-five`` and ``veinticinco``
``25``), on both sides, so a figure cannot slip through spelled out. The words that
are mostly articles or pronouns (``one``, ``un``/``una``/``uno``, ``half``,
``media``) are left out, and Spanish ``once`` (an English word): counting them
would reject nearly every text. Text is
NFKC-normalized first, so full-width digits are ordinary digits, and a vulgar
fraction or other numeric symbol (``½``, ``²``) counts by its value (``0.5``, ``2``).
Grouped thousands (``1,500``) and exponents (``1e3``) read as separate numbers:
that can only reject a figure, never let one through.

``mislabelled_preferences`` is the second check: a preference weight's figure must
say where it comes from -- stated or learned -- as section 40 requires.
"""

import re
import unicodedata
from collections.abc import Collection, Mapping
from decimal import Decimal, InvalidOperation

_TIME = re.compile(r"(?<!\d)(?<!\d:)(\d{1,2}):(\d{2})(?!\d)(?!:\d)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# English and Spanish number words. Left out: the words that are mostly articles or
# pronouns ("one", "un"/"una"/"uno", "half", "media"), and Spanish "once" (11), which
# is an everyday English word. "one"/"uno" still count inside a compound.
_UNITS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
}
_COMPOUND_UNITS = {**_UNITS, "one": 1, "uno": 1, "un": 1, "una": 1}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "veinte": 20,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
}
_OTHER_WORDS = {
    "zero": 0,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "hundred": 100,
    "cero": 0,
    "diez": 10,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veintiuno": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "cien": 100,
}
_WORD_VALUES = {**_UNITS, **_TENS, **_OTHER_WORDS}
_WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")
"""A word, hyphenated words kept whole: ``must-dos`` is not Spanish ``dos``."""


def _without_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _word_numbers(text: str) -> list[int]:
    """The numbers spelled out in ``text``: ``forty``, ``twenty-five``, ``treinta y cinco``.

    A tens word followed by a unit (after a hyphen, a space or Spanish ``y``) is one
    number. Any other hyphenated word is not a number.
    """
    tokens = _WORD.findall(_without_accents(text))
    found: list[int] = []
    i = 0
    while i < len(tokens):
        word = tokens[i]
        if "-" in word:
            tens, _, unit = word.partition("-")
            if tens in _TENS and unit in _COMPOUND_UNITS:
                found.append(_TENS[tens] + _COMPOUND_UNITS[unit])
            i += 1
            continue
        if word in _TENS:
            after = tokens[i + 1 : i + 3]
            unit = after[1:2] if after[:1] == ["y"] else after[:1]
            if unit and unit[0] in _COMPOUND_UNITS:
                found.append(_TENS[word] + _COMPOUND_UNITS[unit[0]])
                i += 2 + (after[:1] == ["y"])
                continue
        if word in _WORD_VALUES:
            found.append(_WORD_VALUES[word])
        i += 1
    return found


def _number(text: str) -> str:
    try:
        value = Decimal(text).normalize()
    except InvalidOperation:  # pragma: no cover - the pattern only yields decimals
        return text
    # normalize() writes 100 as 1E+2; format it back as plain digits.
    return format(value, "f")


def _plain(text: str) -> str:
    """Numeric symbols spelled as their value, then NFKC (full-width digits -> 0-9)."""
    spelled = "".join(
        f" {_number(str(unicodedata.numeric(ch)))} "
        if unicodedata.category(ch) == "No"
        else ch
        for ch in text
    )
    return unicodedata.normalize("NFKC", spelled)


def numerals(text: str) -> set[str]:
    """The normalized clock times and numbers in ``text``."""
    text = _plain(text)
    found = {f"{int(hours)}:{minutes}" for hours, minutes in _TIME.findall(text)}
    without_times = _TIME.sub(" ", text)
    found.update(_number(match) for match in _NUMBER.findall(without_times))
    found.update(str(value) for value in _word_numbers(without_times))
    return found


def ungrounded_numerals(output: str, payload: str) -> list[str]:
    """The numerals in ``output`` that ``payload`` does not contain, sorted."""
    return sorted(numerals(output) - numerals(payload))


_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
_LABEL = re.compile(r"\b(stated|learned)\b", re.IGNORECASE)


def mislabelled_preferences(
    output: str, labelled: Mapping[str, str], ambiguous: Collection[str] = ()
) -> list[str]:
    """Preference figures whose stated/learned label is missing or wrong (section 40).

    ``labelled`` maps a weight's normalized value to its source (``stated`` or
    ``learned``). Wherever the output writes one of those values, the nearest of the
    words "stated" / "learned" in the same sentence must be that source. Values in
    ``ambiguous`` (also a stop figure, or two weights of different sources sharing
    it) cannot be told apart from the number alone and are not checked.
    """
    errors: list[str] = []
    for sentence in _SENTENCE.split(_plain(output)):
        times = [m.span() for m in _TIME.finditer(sentence)]
        labels = [(m.start(), m.group(1).lower()) for m in _LABEL.finditer(sentence)]
        for match in _NUMBER.finditer(sentence):
            if any(start <= match.start() < end for start, end in times):
                continue
            value = _number(match.group())
            source = labelled.get(value)
            if source is None or value in ambiguous:
                continue
            nearest = min(
                labels, key=lambda label: abs(label[0] - match.start()), default=None
            )
            if nearest is None:
                errors.append(f"{value} needs its label ({source})")
            elif nearest[1] != source:
                errors.append(f"{value} is {source}, not {nearest[1]}")
    return errors
