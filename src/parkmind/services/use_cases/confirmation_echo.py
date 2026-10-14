"""Use case: the plain-language echo the guests confirm before constraints are used.

The echo is built here, from the pending entries the extraction produced, and not
in the graph or the API. Accessibility entries are guest-id tokens only
(``accessibility:g2``): they never appear in the echo text, and their ids travel
separately so the UI can ask for consent [C19].
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from parkmind.core.language import DEFAULT_LANGUAGE, Language, guest_ref, text

HEIGHT_PREFIX = "height:"
ACCESSIBILITY_PREFIX = "accessibility:"


@dataclass(frozen=True)
class ConfirmationEcho:
    echo: tuple[str, ...]
    """One sentence per pending constraint, never an accessibility detail."""
    accessibility_guests: tuple[str, ...]
    """Ids of the guests whose accessibility needs await consent."""


def _feet_inches(cm: float) -> str:
    total_inches = round(cm / 2.54)
    return f"{total_inches // 12}'{total_inches % 12}\""


def echo_for(
    entry: str,
    lang: Language = DEFAULT_LANGUAGE,
    *,
    solo: bool = False,
    labels: Mapping[str, str | None] | None = None,
) -> str:
    """Plain-language echo of a non-accessibility pending entry, for confirmation.

    ``labels`` maps a guest id ("g2") to how the guests call them, when they said so.
    """
    kind, _, value = entry.partition(": ")
    if kind in ("must_do", "avoid", "departure_time", "walking_budget"):
        return text(f"echo_{kind}", lang, solo=solo, value=value)
    if kind == "lunch_window":
        start, _, end = value.partition("-")
        return text("echo_lunch_window", lang, start=start, end=end)
    if kind.startswith(HEIGHT_PREFIX):
        guest_id = kind.removeprefix(HEIGHT_PREFIX)
        who = guest_ref(
            lang,
            guest_id.removeprefix("g"),
            (labels or {}).get(guest_id),
            start=True,
        )
        return text(
            "echo_height",
            lang,
            solo=solo,
            who=who,
            value=value,
            feet=_feet_inches(float(value)),
        )
    return entry


def confirmation_echo(
    pending: Sequence[str],
    lang: Language = DEFAULT_LANGUAGE,
    *,
    solo: bool = False,
    labels: Mapping[str, str | None] | None = None,
) -> ConfirmationEcho:
    """Split the pending entries into the echo to show and the accessibility ids."""
    return ConfirmationEcho(
        echo=tuple(
            echo_for(entry, lang, solo=solo, labels=labels)
            for entry in pending
            if not entry.startswith(ACCESSIBILITY_PREFIX)
        ),
        accessibility_guests=tuple(
            sorted(
                entry.removeprefix(ACCESSIBILITY_PREFIX)
                for entry in pending
                if entry.startswith(ACCESSIBILITY_PREFIX)
            )
        ),
    )
