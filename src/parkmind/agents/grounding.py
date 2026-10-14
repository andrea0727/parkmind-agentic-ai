"""Deterministic numeral check for explanations (section 40 [C17], P0-31).

"Every numeral in the explanation is extracted and asserted to be a subset of the
numerals in the supplied payload." No LLM judge sits on this path.

A numeral is a clock time (``14:05``, ``9:05``) or a number (``25``, ``0.8``),
including digits written against letters (``15min``). Both sides are normalized the
same way -- ``25`` and ``25.0`` are one numeral, ``09:05`` and ``9:05`` too -- and
nothing else is allowed: a bare ``14`` is not grounded by ``14:05``, and ``7`` is
not grounded by ``7.4``. Numbers written as words count by their value, on both
sides, so a figure cannot slip through spelled out. The words are read in the text's
own language (``language``, English by default: the narrator writes English), so a
word that is a number in the other language is not one here: English "ten" is 10
but Spanish "ten cuidado" is not, Spanish "once" is 11 but English "once" is not,
and "the dos and don'ts" has no 2. English reads ``forty``, ``twenty-five``,
``forty-minute`` and ``two hundred and five``; Spanish reads ``cuarenta``,
``veintiún``, ``treinta y cinco`` and ``ciento veinte``. The words that are mostly
articles or pronouns (``one``, ``un``/``una``/``uno``) count only inside a
compound, and fractions in words (``half``, ``media``) not at all: counting them
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
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from parkmind.core.language import DEFAULT_LANGUAGE, Language

_TIME = re.compile(r"(?<!\d)(?<!\d:)(\d{1,2}):(\d{2})(?!\d)(?!:\d)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


# English and Spanish number words, one vocabulary per language. Left out of the
# standalone words: the ones that are mostly articles or pronouns ("one",
# "un"/"una"/"uno"); they still count inside a compound ("twenty-one", "ciento uno")
# or before "hundred" ("one hundred").
@dataclass(frozen=True)
class _Vocabulary:
    units: Mapping[str, int]
    """2..9 standalone."""
    compound_units: Mapping[str, int]
    """1..9 after a tens or hundreds word."""
    tens: Mapping[str, int]
    teens: Mapping[str, int]
    """Every other word below a hundred: 0, 10..19 and Spanish 21..29."""
    tens_joiner: str | None = None
    """Spanish "treinta y cinco"."""
    hundreds: Mapping[str, int] = field(default_factory=dict)
    """Spanish hundreds words: "cien", "ciento", "doscientos"..."""
    hundred: str | None = None
    """English "hundred", after an optional unit: "two hundred"."""
    hundred_joiner: str | None = None
    """English "two hundred and five"."""


_EN_UNITS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}
_ES_UNITS = {
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
}
_ENGLISH = _Vocabulary(
    units=_EN_UNITS,
    compound_units={**_EN_UNITS, "one": 1},
    tens={
        "twenty": 20,
        "thirty": 30,
        "forty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
    },
    teens={
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
    },
    hundred="hundred",
    hundred_joiner="and",
)
_SPANISH = _Vocabulary(
    units=_ES_UNITS,
    compound_units={**_ES_UNITS, "uno": 1, "un": 1, "una": 1},
    tens={
        "veinte": 20,
        "treinta": 30,
        "cuarenta": 40,
        "cincuenta": 50,
        "sesenta": 60,
        "setenta": 70,
        "ochenta": 80,
        "noventa": 90,
    },
    teens={
        "cero": 0,
        "diez": 10,
        "once": 11,
        "doce": 12,
        "trece": 13,
        "catorce": 14,
        "quince": 15,
        "dieciseis": 16,
        "diecisiete": 17,
        "dieciocho": 18,
        "diecinueve": 19,
        "veintiun": 21,
        "veintiuno": 21,
        "veintiuna": 21,
        "veintidos": 22,
        "veintitres": 23,
        "veinticuatro": 24,
        "veinticinco": 25,
        "veintiseis": 26,
        "veintisiete": 27,
        "veintiocho": 28,
        "veintinueve": 29,
    },
    tens_joiner="y",
    hundreds={
        "cien": 100,
        "ciento": 100,
        **{
            f"{prefix}ient{ending}": value
            for prefix, value in (
                ("dosc", 200),
                ("tresc", 300),
                ("cuatroc", 400),
                ("seisc", 600),
                ("setec", 700),
                ("ochoc", 800),
                ("novec", 900),
            )
            for ending in ("os", "as")
        },
        "quinientos": 500,
        "quinientas": 500,
    },
)
_VOCABULARIES: dict[Language, _Vocabulary] = {"en": _ENGLISH, "es": _SPANISH}
_WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")
"""A word, hyphenated words kept together (``forty-minute``, ``must-dos``)."""


def _without_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _below_hundred(
    tokens: Sequence[str], i: int, v: _Vocabulary, *, after_hundred: bool = False
) -> tuple[int, int] | None:
    """The number below 100 starting at ``tokens[i]``, and the index after it."""
    if i >= len(tokens):
        return None
    word = tokens[i]
    if word in v.tens:
        j = i + 1
        if v.tens_joiner and j < len(tokens) and tokens[j] == v.tens_joiner:
            j += 1
        if j < len(tokens) and tokens[j] in v.compound_units:
            return v.tens[word] + v.compound_units[tokens[j]], j + 1
        return v.tens[word], i + 1
    if word in v.teens:
        return v.teens[word], i + 1
    units = v.compound_units if after_hundred else v.units
    if word in units:
        return units[word], i + 1
    return None


def _number_at(tokens: Sequence[str], i: int, v: _Vocabulary) -> tuple[int, int] | None:
    """The number starting at ``tokens[i]`` (hundreds included), and the index after it."""
    word = tokens[i]
    hundreds: int | None = None
    if word in v.hundreds:
        hundreds, j = v.hundreds[word], i + 1
    elif v.hundred and word == v.hundred:
        hundreds, j = 100, i + 1
    elif (
        v.hundred
        and word in v.compound_units
        and i + 1 < len(tokens)
        and tokens[i + 1] == v.hundred
    ):
        hundreds, j = v.compound_units[word] * 100, i + 2
    if hundreds is None:
        return _below_hundred(tokens, i, v)
    k = (
        j + 1
        if v.hundred_joiner and j < len(tokens) and tokens[j] == v.hundred_joiner
        else j
    )
    rest = _below_hundred(tokens, k, v, after_hundred=True)
    return (hundreds + rest[0], rest[1]) if rest else (hundreds, j)


def _parse(tokens: Sequence[str], v: _Vocabulary) -> list[int]:
    found: list[int] = []
    i = 0
    while i < len(tokens):
        if (number := _number_at(tokens, i, v)) is not None:
            found.append(number[0])
            i = number[1]
        else:
            i += 1
    return found


def _word_numbers(text: str, language: Language) -> list[int]:
    """The numbers spelled out in ``text``: ``forty``, ``twenty-five``, ``ciento veinte``.

    A hyphenated word is read by its parts (``forty-minute`` is ``40``,
    ``twenty-five-minute`` is ``25``); Spanish doesn't hyphenate numbers, so in
    Spanish text a hyphenated word is no number.
    """
    vocabulary = _VOCABULARIES[language]
    found: list[int] = []
    run: list[str] = []
    for word in _WORD.findall(_without_accents(text)):
        if "-" in word:
            found += _parse(run, vocabulary)
            run = []
            if vocabulary is _ENGLISH:
                found += _parse(word.split("-"), vocabulary)
        else:
            run.append(word)
    return found + _parse(run, vocabulary)


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


def numerals(text: str, language: Language = DEFAULT_LANGUAGE) -> set[str]:
    """The normalized clock times and numbers in ``text``, written in ``language``."""
    text = _plain(text)
    found = {f"{int(hours)}:{minutes}" for hours, minutes in _TIME.findall(text)}
    without_times = _TIME.sub(" ", text)
    found.update(_number(match) for match in _NUMBER.findall(without_times))
    found.update(str(value) for value in _word_numbers(without_times, language))
    return found


def ungrounded_numerals(
    output: str, payload: str, language: Language = DEFAULT_LANGUAGE
) -> list[str]:
    """The numerals in ``output`` that ``payload`` does not contain, sorted.

    Both are read in ``language``, the language the output is written in.
    """
    return sorted(numerals(output, language) - numerals(payload, language))


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
