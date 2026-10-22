"""Reads a guest's short answer to "what time do you plan to leave?" without the LLM.

A bare "18", "18:00", "6 pm", "6pm", "a las 6", "6 de la tarde" or "18h" carries no context for
the extractor (it never sees the question), so the answer is read here. Leaving the park is
always an afternoon or evening matter: a bare hour from 1 to 11 means PM.
"""

import re

_FILLER = frozenset(
    [
        "a",
        "las",
        "la",
        "at",
        "around",
        "about",
        "by",
        "like",
        "como",
        "tipo",
        "hacia",
        "sobre",
        "eso",
        "de",
        "en",
        "to",
        "the",
        "pues",
        "es",
        "seria",
        "sería",
        "salimos",
        "salgo",
        "salir",
        "leave",
        "leaving",
        "we",
        "i",
    ]
)
_TIME = re.compile(
    r"(?<![\d:.])(\d{1,2})(?:\s*[:.h]\s*(\d{2}))?(?:\s*(?:hs?|hrs?|horas?))?"
    r"(?:\s*(a\.?\s?m\.?|p\.?\s?m\.?|de la ma[ñn]ana|de la tarde|de la noche|en la noche|"
    r"a\.m|p\.m))?(?![\d:])",
    re.IGNORECASE,
)
_HALF = re.compile(r"\by\s+media\b|\bhalf\s+past\b", re.IGNORECASE)
_WORD = re.compile(r"[a-záéíóúñ]+", re.IGNORECASE)


def parse_clock(answer: str) -> str | None:
    """``"HH:MM"`` when the answer is exactly one clock time, else ``None``."""
    matches = list(_TIME.finditer(answer))
    if len(matches) != 1:
        return None
    found = matches[0]
    hour = int(found.group(1))
    minute = int(found.group(2)) if found.group(2) else 0
    marker = (found.group(3) or "").lower().replace(".", "").replace(" ", "")
    explicit = (
        found.group(2) is not None
        or bool(marker)
        or bool(re.search(r"\d\s*(?:hs?|hrs?|horas?)\b", answer, re.IGNORECASE))
    )
    if not explicit:
        rest = _TIME.sub(" ", answer)
        rest = _HALF.sub(" ", rest)
        if any(word.lower() not in _FILLER for word in _WORD.findall(rest)):
            return None
    if _HALF.search(answer) and found.group(2) is None:
        minute = 30
    if marker in ("pm", "delatarde", "delanoche", "enlanoche") and hour < 12:
        hour += 12
    elif marker in ("am", "delamañana", "delamanana") and hour == 12:
        hour = 0
    elif not marker and 1 <= hour <= 11:
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"
