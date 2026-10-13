"""Deterministic numeral check for explanations (section 40 [C17], P0-31).

"Every numeral in the explanation is extracted and asserted to be a subset of the
numerals in the supplied payload." No LLM judge sits on this path.

A numeral is a clock time (``14:05``, ``9:05``) or a number (``25``, ``0.8``),
including digits written against letters (``15min``). Both sides are normalized the
same way -- ``25`` and ``25.0`` are one numeral, ``09:05`` and ``9:05`` too -- and
nothing else is allowed: a bare ``14`` is not grounded by ``14:05``, and ``7`` is
not grounded by ``7.4``. Numbers written in words are not numerals. Text is
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
