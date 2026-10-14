"""Deterministic numeral check for explanations (section 40 [C17], P0-31).

"Every numeral in the explanation is extracted and asserted to be a subset of the
numerals in the supplied payload." No LLM judge sits on this path.

A numeral is a clock time (``14:05``, ``9:05``) or a number (``25``, ``0.8``),
including digits written against letters (``15min``). Both sides are normalized the
same way -- ``25`` and ``25.0`` are one numeral, ``09:05`` and ``9:05`` too -- and
nothing else is allowed: a bare ``14`` is not grounded by ``14:05``, and ``7`` is
not grounded by ``7.4``. Numbers written in words (``ten``, ``half``, ``dozen``; see
``_WORDS``) count by their value, on both sides, so a ride named "Seven Dwarfs" is
grounded by its own name; ``one`` is left out because it is an ordinary pronoun.
Text is NFKC-normalized first, so full-width digits are ordinary digits, and a vulgar
fraction or other numeric symbol (``½``, ``²``) counts by its value (``0.5``, ``2``).
Grouped thousands (``1,500``) and exponents (``1e3``) read as separate numbers.

The check is a subset test, so it cannot tell a figure from another written the same
way: ``3 PM`` is read as ``3``, ``½`` as ``0.5``, ``1,500`` as ``1`` and ``500``, and
all of them pass when the payload already contains those numbers (a stop count, a
3 minute wait). Swapped figures (the 25 minute wait written as 45) pass too when
both are in the payload. It rejects an invented figure, not every misuse of a real one.

``mislabelled_preferences`` is the second check: a preference weight's figure must
say where it comes from -- stated or learned -- as section 40 requires, and a
``default`` weight must not be called either.
"""

import re
import unicodedata
from collections.abc import Collection, Mapping
from decimal import Decimal, InvalidOperation

_TIME = re.compile(r"(?<!\d)(?<!\d:)(\d{1,2}):(\d{2})(?!\d)(?!:\d)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_WORDS = {
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "half": "0.5",
    "dozen": "12",
    "hundred": "100",
}
_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)


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
    found.update(_WORDS[word.lower()] for word in _WORD.findall(text))
    return found


def ungrounded_numerals(output: str, payload: str) -> list[str]:
    """The numerals in ``output`` that ``payload`` does not contain, sorted."""
    return sorted(numerals(output) - numerals(payload))


_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
_LABEL = re.compile(r"\b(stated|learned)\b", re.IGNORECASE)
_ITEM = re.compile(r"^\s*[-*•]\s+")


def _units(text: str) -> list[tuple[str, str | None]]:
    """Each sentence or list item, with the label of the header it sits under.

    "Your stated priorities:" followed by ``- thrill 0.8`` labels the item; an item's
    own label, when it has one, still wins.
    """
    units: list[tuple[str, str | None]] = []
    inherited: str | None = None
    for line in text.split("\n"):
        if not line.strip():
            continue
        item = _ITEM.match(line) is not None
        if not item:
            found = {m.group(1).lower() for m in _LABEL.finditer(line)}
            header = line.rstrip().endswith(":") and len(found) == 1
            inherited = next(iter(found)) if header else None
        units.extend((s, inherited if item else None) for s in _SENTENCE.split(line))
    return units


def mislabelled_preferences(
    output: str, labelled: Mapping[str, str], ambiguous: Collection[str] = ()
) -> list[str]:
    """Preference figures whose stated/learned label is missing or wrong (section 40).

    ``labelled`` maps a weight's normalized value to its source (``stated``,
    ``learned`` or ``default``). Wherever the output writes one of those values, the
    nearest of the words "stated" / "learned" in the same sentence (or, for a list
    item, its header line) must be that source; a ``default`` weight must have none
    near it. Values in ``ambiguous`` (also a stop figure, or two weights of different
    sources sharing it) cannot be told apart from the number alone and are not checked.
    """
    errors: list[str] = []
    for sentence, header in _units(_plain(output)):
        times = [m.span() for m in _TIME.finditer(sentence)]
        labels = [(m.start(), m.group(1).lower()) for m in _LABEL.finditer(sentence)]
        if not labels and header:
            labels = [(0, header)]
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
                if source != "default":
                    errors.append(f"{value} needs its label ({source})")
            elif nearest[1] != source:
                errors.append(f"{value} is {source}, not {nearest[1]}")
    return errors
