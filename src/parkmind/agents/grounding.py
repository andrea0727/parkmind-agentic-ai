"""Deterministic numeral check for explanations (section 40 [C17], P0-31).

"Every numeral in the explanation is extracted and asserted to be a subset of the
numerals in the supplied payload." No LLM judge sits on this path.

A numeral is a clock time (``14:05``, ``9:05``) or a number (``25``, ``0.8``),
including digits written against letters (``15min``). Both sides are normalized the
same way -- ``25`` and ``25.0`` are one numeral, ``09:05`` and ``9:05`` too -- and
nothing else is allowed: a bare ``14`` is not grounded by ``14:05``, and ``7`` is
not grounded by ``7.4``. Numbers written in words are not numerals.
"""

import re
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


def numerals(text: str) -> set[str]:
    """The normalized clock times and numbers in ``text``."""
    found = {f"{int(hours)}:{minutes}" for hours, minutes in _TIME.findall(text)}
    without_times = _TIME.sub(" ", text)
    found.update(_number(match) for match in _NUMBER.findall(without_times))
    return found


def ungrounded_numerals(output: str, payload: str) -> list[str]:
    """The numerals in ``output`` that ``payload`` does not contain, sorted."""
    return sorted(numerals(output) - numerals(payload))
