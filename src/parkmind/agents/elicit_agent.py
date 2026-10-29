"""``elicit``: natural language -> structured guest and planning information.

The LLM only classifies and extracts into ``ElicitationExtraction``. This
module then turns the validated extraction into contracts and decides what is
still missing. It never decides what the checker enforces:

* soft preferences become ``GuestProfile`` values (stated, tradeable);
* every hard item -- including a stated height and all accessibility flags -- is only *pending*:
  it is listed in ``pending_hard_constraint_confirmation`` and the graph
  confirms it with the human before ``constraints_valid`` can become True.
* accessibility flags are staged through ``AccessibilityIntakeUseCase``, never
  written to state, and never stored as soft preferences [C15, C19].
"""

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Any, Protocol

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from parkmind.agents.ask_agent import QuestionWriter, Turn, word_question
from parkmind.agents.ask_template import plain_question
from parkmind.agents.clock_answer import parse_clock
from parkmind.agents.elicit_prompt import ELICIT_PROMPT_VERSION, ELICIT_SYSTEM_PROMPT
from parkmind.agents.elicit_schema import ElicitationExtraction, normalize_name
from parkmind.agents.llm_budget import (
    LlmBudgetExhaustedError,
    LlmCallBudget,
    bound_to_thread,
    invoke_counted,
)
from parkmind.agents.llm_client import build_chat_model
from parkmind.agents.time_grounding import (
    departure_is_grounded,
    lunch_window_is_grounded,
    walking_budget_is_grounded,
)
from parkmind.agents.walking_answer import parse_walking_answer
from parkmind.config.settings import settings
from parkmind.core.contracts import (
    PARK_TZ,
    Guest,
    GuestProfile,
    PartyConstraints,
    PlanningPace,
    PlanningStyle,
    PreferenceSource,
    PreferenceValue,
    TimeWindow,
)
from parkmind.core.language import (
    DEFAULT_LANGUAGE,
    Language,
    detect_language,
    guest_ref,
    text,
)
from parkmind.graph.state import ParkMindState
from parkmind.services.use_cases.accessibility_intake import (
    AccessibilityIntakeUseCase,
    StagedAccessibility,
)
from parkmind.services.use_cases.confirmation_echo import (
    ACCESSIBILITY_PREFIX,
    HEIGHT_PREFIX,
)
from parkmind.services.use_cases.planning_deps import PlanningUnavailableError
from parkmind.services.use_cases.resolve_attraction_names import (
    AttractionNameResolver,
    NameResolution,
)

__all__ = [
    "ELICIT_PROMPT_VERSION",
    "AnthropicExtractor",
    "ElicitNode",
    "ExtractionOutcome",
    "ExtractorOutputError",
    "ExtractorUnavailableError",
    "GuestInfoExtractor",
    "build_guest_profiles",
    "build_party_constraints",
    "conflicting_names",
    "extract_with_recovery",
    "heights_needed",
    "human_texts",
    "make_elicit_node",
    "missing_information",
    "oversized_party",
    "pending_confirmations",
    "ungrounded_heights",
    "ungrounded_times",
    "unresolved_names",
]

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 2
_DEFAULT_TOLERANCE = 0.5
_STATED_CONFIDENCE = 0.8

_MISSING_QUESTIONS = {
    "guests": "q_guests",
    "departure_time": "q_departure_time",
    "unreadable_response": "q_unreadable_response",
}


class ElicitNode(Protocol):
    def __call__(self, state: ParkMindState) -> dict[str, Any]: ...


class ExtractorOutputError(ValueError):
    """The extractor returned no usable structured output (retried like a schema failure)."""


class ExtractorUnavailableError(RuntimeError):
    """The model could not be reached (not a bad answer): retrying the same call will not help."""


class GuestInfoExtractor(Protocol):
    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        """Raw structured output for the guests' messages; validated by the caller."""
        ...


_ACCESS_FLAGS = (
    "daily_walking_limit_minutes",
    "rest_frequency_minutes",
    "mobility_requirements",
    "heat_sensitivity",
    "ride_restrictions",
)


def without_empty_accessibility(args: Mapping[str, Any]) -> Mapping[str, Any]:
    """Drop accessibility entries that set no flag: with thinking off the model pads every guest.

    An entry with no flag says "no restriction", so dropping it never weakens a constraint.
    """
    entries = args.get("accessibility")
    if not isinstance(entries, list):
        return args
    kept = [
        e
        for e in entries
        if not isinstance(e, Mapping) or any(e.get(f) for f in _ACCESS_FLAGS)
    ]
    return {**args, "accessibility": kept}


SDK_RETRIES = 2
"""Transient-failure retries per extraction (counted, like any call, against the thread's budget)."""


class AnthropicExtractor:
    """Tool-calling extractor. The client is built on first use."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        *,
        budget: LlmCallBudget | None = None,
    ) -> None:
        self._model = model or settings.ELICIT_MODEL
        self._api_key = api_key or settings.LLM_API_KEY
        self._budget = budget
        self._llm: Any = None

    def _runnable(self) -> Any:
        if self._llm is None:
            chat = build_chat_model(
                model=self._model,
                api_key=self._api_key,
                temperature=0,
                max_tokens=1500,
                timeout=60,
            )
            self._llm = chat.bind_tools(
                [ElicitationExtraction], tool_choice=ElicitationExtraction.__name__
            )
        return self._llm

    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        listing = "\n".join(
            f"{i}. {text}" for i, text in enumerate(human_messages, start=1)
        )
        content = f"Guest messages, oldest first:\n{listing}"
        if repair_hint:
            content += (
                "\n\nYour previous output was rejected by validation. Fix exactly these "
                f"problems and return the full corrected output:\n{repair_hint}"
            )
        try:
            response = invoke_counted(
                self._runnable(),
                [SystemMessage(ELICIT_SYSTEM_PROMPT), HumanMessage(content)],
                retries=SDK_RETRIES,
                budget=self._budget,
            )
        except LlmBudgetExhaustedError:
            raise
        except Exception as exc:
            raise ExtractorUnavailableError(type(exc).__name__) from exc
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            raise ExtractorOutputError("the model returned no structured output")
        args = calls[0].get("args")
        if not isinstance(args, Mapping):
            raise ExtractorOutputError("the model returned malformed structured output")
        return without_empty_accessibility(args)


@dataclass(frozen=True)
class ExtractionOutcome:
    extraction: ElicitationExtraction | None
    attempts: int
    errors: tuple[str, ...] = ()


def _summarize(exc: Exception) -> tuple[str, ...]:
    """Error text without input values: user words must not leak into logs or prompts."""
    if isinstance(exc, ValidationError):
        return tuple(
            f"{'.'.join(str(p) for p in err['loc']) or 'output'}: {err['msg']}"
            for err in exc.errors(include_input=False)
        )
    return (str(exc),)


def extract_with_recovery(
    extractor: GuestInfoExtractor,
    human_messages: Sequence[str],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ExtractionOutcome:
    """Validate the extractor's output; on failure retry with the errors as a repair hint.

    A schema failure never raises: after ``max_attempts`` the outcome has
    ``extraction=None`` and the graph asks the guests to rephrase.
    """
    hint: str | None = None
    errors: tuple[str, ...] = ()
    for attempt in range(1, max_attempts + 1):
        try:
            raw = extractor.extract(human_messages, hint)
            return ExtractionOutcome(ElicitationExtraction.model_validate(raw), attempt)
        except (ValidationError, ExtractorOutputError) as exc:
            errors = _summarize(exc)
            hint = "\n".join(f"- {e}" for e in errors)
    return ExtractionOutcome(None, max_attempts, errors)


def missing_information(extraction: ElicitationExtraction) -> list[str]:
    """Required inputs the guests did not give. Surfaced, never guessed."""
    missing: list[str] = []
    if not extraction.guests:
        missing.append("guests")
    for index, guest in enumerate(extraction.guests, start=1):
        if guest.role is None:
            missing.append(f"guest_role:g{index}")
    if extraction.departure_time is None:
        missing.append("departure_time")
    return missing


_UNKNOWN_ATTRACTION = "unknown_attraction:"
_AMBIGUOUS_ATTRACTION = "ambiguous_attraction:"
_CONFLICTING_ATTRACTION = "conflicting_attraction:"
_UNVERIFIED_HEIGHT = "height_unverified:"
_HEIGHT_NEEDED = "height_needed:"
_PARTY_TOO_LARGE = "party_too_large:"
_UNVERIFIED_DEPARTURE = "departure_unverified"
_UNVERIFIED_LUNCH = "lunch_window_unverified"
_UNVERIFIED_WALKING_BUDGET = "walking_budget_unverified"
_LUNCH_AFTER_DEPARTURE = "lunch_after_departure:"

_HEIGHT_TOLERANCE_CM = 1.5
_CM_PER_FOOT = 30.48
_CM_PER_INCH = 2.54
_NUMBER = r"(\d+(?:[.,]\d+)?)"
_CM_RE = re.compile(rf"{_NUMBER}\s*(?:cm|cms|centimet\w*|centím\w*)\b", re.IGNORECASE)
_METERS_RE = re.compile(
    rf"{_NUMBER}\s*(?:m|mt|mts|meters?|metres?|metros?)\b", re.IGNORECASE
)
_FEET_RE = re.compile(
    r"(\d)\s*(?:'|’|ft\b|feet\b|foot\b|-foot\b|pies?\b)\s*(?:(\d{1,2})\s*(?:\"|in\b|inch\w*|pulgadas?\b)?)?",
    re.IGNORECASE,
)
_DECIMAL_METERS_RE = re.compile(r"(?<![\d.,])([12][.,]\d{1,2})(?!\d|[.,]\d)")
_BARE_RE = re.compile(
    r"(?<![\d.,:])(\d{2,3})(?!\d|[.,:]\d)(?!\s*(?:min|minutes?|minutos?|hours?|horas?|hrs?|h\b|pm|am|%|years?|años?|anos?|yo\b))",
    re.IGNORECASE,
)


def _as_float(raw: str) -> float:
    return float(raw.replace(",", "."))


def heights_mentioned_cm(text: str) -> list[float]:
    """Every height, in cm, that the text could be stating (cm, m, feet/inches or a bare cm)."""
    found: list[float] = []
    found.extend(_as_float(m.group(1)) for m in _CM_RE.finditer(text))
    found.extend(_as_float(m.group(1)) * 100 for m in _METERS_RE.finditer(text))
    found.extend(_as_float(m.group(1)) * 100 for m in _DECIMAL_METERS_RE.finditer(text))
    for m in _FEET_RE.finditer(text):
        inches = int(m.group(2)) if m.group(2) else 0
        found.append(int(m.group(1)) * _CM_PER_FOOT + inches * _CM_PER_INCH)
    found.extend(float(m.group(1)) for m in _BARE_RE.finditer(text))
    return found


def ungrounded_heights(
    extraction: ElicitationExtraction, texts: Sequence[str]
) -> list[str]:
    """Missing-information keys for heights the guests never wrote.

    Height decides who may ride, so the model's value must trace back to the
    guests' own words. An out-of-range figure ("400 cm") that the model clamped
    into the schema's range, or one it invented, matches nothing they typed.
    """
    stated = [h for text in texts for h in heights_mentioned_cm(text)]
    return [
        f"{_UNVERIFIED_HEIGHT}g{index}"
        for index, guest in enumerate(extraction.guests, start=1)
        if guest.height_cm is not None
        and not any(abs(guest.height_cm - h) <= _HEIGHT_TOLERANCE_CM for h in stated)
    ]


def ungrounded_times(
    extraction: ElicitationExtraction,
    texts: Sequence[str],
    *,
    clock_answer: str | None = None,
    walking_answer: int | None = None,
) -> list[str]:
    """Missing-information keys for a departure time, lunch window or walking budget the guests never wrote [G13].

    ``clock_answer`` is the departure read straight from a bare reply to "what
    time do you leave?", and ``walking_answer`` the minutes read from a reply to the
    walking question; each is the guests' own answer, so it is grounded by construction.
    """
    missing: list[str] = []
    departure = extraction.departure_time
    if (
        departure is not None
        and departure != clock_answer
        and not departure_is_grounded(departure, texts)
    ):
        missing.append(_UNVERIFIED_DEPARTURE)
    window = extraction.lunch_window
    if window is not None and not lunch_window_is_grounded(
        window.start, window.end, texts
    ):
        missing.append(_UNVERIFIED_LUNCH)
    budget = extraction.party_walking_budget_minutes
    if (
        budget is not None
        and budget != walking_answer
        and not walking_budget_is_grounded(budget, texts)
    ):
        missing.append(_UNVERIFIED_WALKING_BUDGET)
    return missing


def lunch_after_departure(extraction: ElicitationExtraction) -> list[str]:
    """``lunch_after_departure:<start>|<end>|<departure>`` when lunch starts once the party has left.

    The two times contradict each other, so the guests are asked which one to
    change instead of finding out at the plan check. "HH:MM" strings are zero-padded,
    so they compare as times.
    """
    window, departure = extraction.lunch_window, extraction.departure_time
    if window is None or departure is None or window.start < departure:
        return []
    return [f"{_LUNCH_AFTER_DEPARTURE}{window.start}|{window.end}|{departure}"]


def unresolved_names(resolution: NameResolution) -> list[str]:
    """Missing-information keys for must-do/avoid names that match no single attraction."""
    return [f"{_UNKNOWN_ATTRACTION}{name}" for name in resolution.unknown] + [
        f"{_AMBIGUOUS_ATTRACTION}{name}" for name in resolution.ambiguous
    ]


def oversized_party(extraction: ElicitationExtraction) -> list[str]:
    """``party_too_large:<size>`` when the party is over ``settings.MAX_PARTY_SIZE``, else nothing."""
    size = max(extraction.party_size or 0, len(extraction.guests))
    return [f"{_PARTY_TOO_LARGE}{size}"] if size > settings.MAX_PARTY_SIZE else []


def heights_needed(
    extraction: ElicitationExtraction, resolution: NameResolution
) -> list[str]:
    """Missing-information keys for must-do rides with a minimum height while a guest's height is unknown.

    Only asked about must-dos: the checker fails closed for a guest without a
    height, so a must-do ride they might not clear would leave no valid plan.
    Avoided rides are never planned, so their height limit is irrelevant.
    """
    if not any(guest.height_cm is None for guest in extraction.guests):
        return []
    restricted = dict.fromkeys(
        resolution.resolved[name].name
        for name in extraction.must_do
        if name in resolution.resolved
        and resolution.resolved[name].height_restriction_cm is not None
    )
    return [f"{_HEIGHT_NEEDED}{name}" for name in restricted]


def conflicting_names(
    extraction: ElicitationExtraction, resolution: NameResolution
) -> list[str]:
    """Missing-information keys for attractions that are both must-do and avoid.

    Compared by resolved ``node_id`` (two spoken names can be one attraction).
    Such a request has no valid plan, so the guests are asked which one they meant.
    """
    wanted = {
        resolution.resolved[name].node_id
        for name in extraction.must_do
        if name in resolution.resolved
    }
    official = dict.fromkeys(
        resolution.resolved[name].name
        for name in extraction.avoid
        if name in resolution.resolved and resolution.resolved[name].node_id in wanted
    )
    return [f"{_CONFLICTING_ATTRACTION}{name}" for name in official]


def _question_for(
    missing: Sequence[str],
    resolution: NameResolution | None = None,
    lang: Language = DEFAULT_LANGUAGE,
    party_size: int = 0,
    labels: Sequence[str | None] = (),
) -> str:
    questions: list[str] = []
    solo = party_size == 1

    def who(key_ref: str, *, start: bool = False) -> str:
        number = key_ref.removeprefix("g")
        index = int(number) - 1 if number.isdigit() else -1
        label = labels[index] if 0 <= index < len(labels) else None
        return guest_ref(lang, number, label, start=start)

    minimum_by_name = (
        {
            r.name: r.height_restriction_cm
            for r in resolution.resolved.values()
            if r.height_restriction_cm is not None
        }
        if resolution is not None
        else {}
    )
    height_question_added = False
    for key in missing:
        if key.startswith(_HEIGHT_NEEDED):
            if height_question_added:
                continue
            height_question_added = True
            rides = ", ".join(
                text("height_minimum", lang, name=name, cm=minimum_by_name[name])
                for name in (
                    k.removeprefix(_HEIGHT_NEEDED)
                    for k in missing
                    if k.startswith(_HEIGHT_NEEDED)
                )
                if name in minimum_by_name
            )
            questions.append(text("q_height_needed", lang, solo=solo, rides=rides))
        elif key.startswith(_PARTY_TOO_LARGE):
            questions.append(
                text(
                    "q_party_too_large",
                    lang,
                    size=key.removeprefix(_PARTY_TOO_LARGE),
                    max=settings.MAX_PARTY_SIZE,
                )
            )
        elif key.startswith("guest_role:"):
            if solo:
                questions.append(text("q_guest_role_solo", lang))
            else:
                questions.append(
                    text(
                        "q_guest_role",
                        lang,
                        # Spanish opens with the guest ("¿Tu mamá es…"); English with "Is".
                        who=who(key.split(":", 1)[1], start=lang == "es"),
                    )
                )
        elif key.startswith(_UNKNOWN_ATTRACTION):
            name = key.removeprefix(_UNKNOWN_ATTRACTION)
            groups = (
                "; ".join(
                    f"{text(f'attraction_group_{category}', lang)} ({', '.join(names)})"
                    for category, names in resolution.options_by_category.items()
                )
                if resolution is not None
                else ""
            )
            suggestions = (
                text("attraction_options", lang, groups=groups) if groups else ""
            )
            questions.append(
                text(
                    "q_unknown_attraction",
                    lang,
                    solo=solo,
                    name=name,
                    options=suggestions,
                )
            )
        elif key.startswith(_AMBIGUOUS_ATTRACTION):
            name = key.removeprefix(_AMBIGUOUS_ATTRACTION)
            options = resolution.ambiguous[name] if resolution is not None else ()
            questions.append(
                text(
                    "q_ambiguous_attraction",
                    lang,
                    name=name,
                    options=", ".join(options),
                )
            )
        elif key.startswith(_UNVERIFIED_HEIGHT):
            guest = key.removeprefix(_UNVERIFIED_HEIGHT)
            questions.append(
                text("q_unverified_height", lang, solo=solo, who=who(guest))
            )
        elif key == _UNVERIFIED_DEPARTURE:
            questions.append(text("q_unverified_departure", lang, solo=solo))
        elif key == _UNVERIFIED_LUNCH:
            questions.append(text("q_unverified_lunch", lang, solo=solo))
        elif key == _UNVERIFIED_WALKING_BUDGET:
            questions.append(text("q_unverified_walking_budget", lang, solo=solo))
        elif key.startswith(_LUNCH_AFTER_DEPARTURE):
            start, end, departure = key.removeprefix(_LUNCH_AFTER_DEPARTURE).split("|")
            questions.append(
                text(
                    "q_lunch_after_departure",
                    lang,
                    solo=solo,
                    start=start,
                    end=end,
                    departure=departure,
                )
            )
        elif key.startswith(_CONFLICTING_ATTRACTION):
            name = key.removeprefix(_CONFLICTING_ATTRACTION)
            questions.append(
                text("q_conflicting_attraction", lang, solo=solo, name=name)
            )
        elif key in _MISSING_QUESTIONS:
            questions.append(text(_MISSING_QUESTIONS[key], lang, solo=solo))
        else:
            questions.append(text("q_generic", lang, solo=solo, key=key))
    return " ".join(questions)


def _at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=PARK_TZ)


def build_party_constraints(
    extraction: ElicitationExtraction,
    *,
    today: date,
    resolution: NameResolution,
    constraints_version: int = 1,
) -> PartyConstraints:
    """Build the contract. Call only when ``missing_information`` is empty and
    ``resolution`` is complete: must_do/avoid hold catalog ``node_id``s."""
    guests = [
        Guest(guest_id=f"g{i}", role=g.role, height_cm=g.height_cm, label=g.label)  # type: ignore[arg-type]
        for i, g in enumerate(extraction.guests, start=1)
    ]
    window = extraction.lunch_window
    assert extraction.departure_time is not None
    return PartyConstraints(
        party_size=extraction.party_size or len(guests),
        guests=guests,
        must_do=resolution.node_ids(extraction.must_do),
        avoid=resolution.node_ids(extraction.avoid),
        lunch_window=TimeWindow(
            start=_at(today, window.start), end=_at(today, window.end)
        )
        if window
        else None,
        departure_time=_at(today, extraction.departure_time),
        party_walking_budget_minutes=extraction.party_walking_budget_minutes,
        constraints_version=constraints_version,
    )


def _stated(value: float, now: datetime) -> PreferenceValue:
    return PreferenceValue(
        value=value,
        source=PreferenceSource.STATED,
        confidence=_STATED_CONFIDENCE,
        updated_at=now,
        stated_value=value,
    )


def _tolerance(value: float | None, now: datetime) -> PreferenceValue:
    if value is not None:
        return _stated(value, now)
    return PreferenceValue(
        value=_DEFAULT_TOLERANCE,
        source=PreferenceSource.DEFAULT,
        confidence=0.0,
        updated_at=now,
    )


def build_guest_profiles(
    extraction: ElicitationExtraction, *, now: datetime
) -> list[GuestProfile]:
    """Soft preferences only. Accessibility never appears here."""
    profiles: list[GuestProfile] = []
    for index, guest in enumerate(extraction.guests, start=1):
        profiles.append(
            GuestProfile(
                guest_id=f"g{index}",
                pace=guest.pace or PlanningPace.BALANCED,
                queue_tolerance=_tolerance(guest.queue_tolerance, now),
                walking_tolerance=_tolerance(guest.walking_tolerance, now),
                sensitivities={s.kind: s.level for s in guest.sensitivities},
                thematic_affinity={
                    normalize_name(a.theme): _stated(a.affinity, now)
                    for a in guest.thematic_affinity
                },
                preferred_categories=list(dict.fromkeys(guest.preferred_categories)),
                avoided_categories=list(dict.fromkeys(guest.avoided_categories)),
                planning_style=guest.planning_style or PlanningStyle.FLEXIBLE,
                profile_version=1,
            )
        )
    return profiles


def pending_confirmations(
    extraction: ElicitationExtraction, resolution: NameResolution
) -> list[str]:
    """Every hard item the human must confirm before the checker may use it.

    must_do/avoid entries carry the attraction's official name, so the human
    confirms what will actually be enforced.

    Accessibility entries are guest-id tokens only (``accessibility:g2``): the
    list is checkpointed state and must not carry the flags themselves [C19].
    """
    pending: list[str] = []
    for kind, spoken in (("must_do", extraction.must_do), ("avoid", extraction.avoid)):
        official = dict.fromkeys(resolution.resolved[name].name for name in spoken)
        pending.extend(f"{kind}: {name}" for name in official)
    if extraction.departure_time is not None:
        pending.append(f"departure_time: {extraction.departure_time}")
    if extraction.lunch_window is not None:
        window = extraction.lunch_window
        pending.append(f"lunch_window: {window.start}-{window.end}")
    if extraction.party_walking_budget_minutes is not None:
        pending.append(f"walking_budget: {extraction.party_walking_budget_minutes}")
    pending.extend(
        f"{HEIGHT_PREFIX}g{index}: {guest.height_cm:g}"
        for index, guest in enumerate(extraction.guests, start=1)
        if guest.height_cm is not None
    )
    pending.extend(
        f"{ACCESSIBILITY_PREFIX}g{ref}"
        for ref in sorted({a.guest_ref for a in extraction.accessibility})
    )
    return pending


def human_texts(state: ParkMindState) -> list[str]:
    return [
        m.content
        for m in state.get("messages", [])
        if getattr(m, "type", None) == "human" and isinstance(m.content, str)
    ]


def _staged(extraction: ElicitationExtraction) -> list[StagedAccessibility]:
    merged: dict[int, dict[str, Any]] = {}
    for entry in extraction.accessibility:
        slot = merged.setdefault(
            entry.guest_ref,
            {
                "mobility": [],
                "restrictions": [],
                "limit": None,
                "rest": None,
                "heat": False,
            },
        )
        slot["mobility"].extend(entry.mobility_requirements)
        slot["restrictions"].extend(entry.ride_restrictions)
        if entry.daily_walking_limit_minutes is not None:
            slot["limit"] = entry.daily_walking_limit_minutes
        if entry.rest_frequency_minutes is not None:
            slot["rest"] = entry.rest_frequency_minutes
        slot["heat"] = slot["heat"] or entry.heat_sensitivity
    return [
        StagedAccessibility(
            guest_id=f"g{ref}",
            daily_walking_limit_minutes=slot["limit"],
            rest_frequency_minutes=slot["rest"],
            mobility_requirements=tuple(dict.fromkeys(slot["mobility"])),
            heat_sensitivity=slot["heat"],
            ride_restrictions=tuple(dict.fromkeys(slot["restrictions"])),
        )
        for ref, slot in sorted(merged.items())
    ]


def _asked_for_departure(state: ParkMindState) -> bool:
    messages = state.get("messages", [])
    last = messages[-2] if len(messages) >= 2 else None
    asked = getattr(last, "additional_kwargs", {}).get("missing_information") or ()
    return getattr(last, "type", None) == "ai" and (
        "departure_time" in asked or _UNVERIFIED_DEPARTURE in asked
    )


def _asked_for_heights(state: ParkMindState) -> bool:
    """Heights are asked once: a guest who does not answer is left to the checker, which fails closed."""
    return any(
        getattr(m, "type", None) == "ai"
        and any(
            str(key).startswith(_HEIGHT_NEEDED)
            for key in getattr(m, "additional_kwargs", {}).get("missing_information")
            or ()
        )
        for m in state.get("messages", [])
    )


def _asked_for_walking(state: ParkMindState) -> bool:
    """The last question asked for the walking limit, and not for the departure as well.

    A bare "18" answers the departure; with both asked, the reply is left to the extractor.
    """
    messages = state.get("messages", [])
    last = messages[-2] if len(messages) >= 2 else None
    asked = getattr(last, "additional_kwargs", {}).get("missing_information") or ()
    return (
        getattr(last, "type", None) == "ai"
        and _UNVERIFIED_WALKING_BUDGET in asked
        and not _asked_for_departure(state)
    )


def _with_walking_answer(
    outcome: ExtractionOutcome, state: ParkMindState, answer: str
) -> tuple[ExtractionOutcome, int | None]:
    """A short reply to "how many minutes can you walk?" is read here [G13].

    The extractor never sees the question, and a bare "30" names no unit, so it could
    never be grounded and the question came back forever. The reply is the guests' own
    answer, so the minutes it states are grounded by construction (like ``clock_answer``).
    """
    extraction = outcome.extraction
    if extraction is None or not _asked_for_walking(state):
        return outcome, None
    reply = parse_walking_answer(answer)
    if reply is None:
        return outcome, None
    update = {"party_walking_budget_minutes": reply.minutes}
    return replace(
        outcome, extraction=extraction.model_copy(update=update)
    ), reply.minutes


def _with_clock_answer(
    outcome: ExtractionOutcome, state: ParkMindState, answer: str
) -> ExtractionOutcome:
    """A short reply to "what time do you leave?" is read here: the extractor never sees the question."""
    extraction = outcome.extraction
    if extraction is None or not _asked_for_departure(state):
        return outcome
    hhmm = parse_clock(answer)
    if hhmm is None:
        return outcome
    return replace(
        outcome, extraction=extraction.model_copy(update={"departure_time": hhmm})
    )


UNAVAILABLE_FLAG = "unavailable"


def _unavailable(key: str, lang: Language) -> dict[str, Any]:
    """End the turn with a plain message: a service is down, so there is nothing to ask."""
    return {
        "language": lang,
        "constraints": None,
        "constraints_valid": False,
        "pending_hard_constraint_confirmation": None,
        "messages": [
            AIMessage(
                content=text(key, lang), additional_kwargs={UNAVAILABLE_FLAG: True}
            )
        ],
    }


def _turns(state: ParkMindState) -> list[Turn]:
    """The conversation so far as (role, text), for wording the next question."""
    turns: list[Turn] = []
    for message in state.get("messages") or []:
        kind = getattr(message, "type", None)
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip() and kind in ("human", "ai"):
            turns.append(("guest" if kind == "human" else "assistant", content))
    return turns


def make_elicit_node(
    extractor: GuestInfoExtractor,
    intake: AccessibilityIntakeUseCase,
    names: AttractionNameResolver,
    *,
    asker: QuestionWriter | None = None,
    today: Callable[[], date] = lambda: datetime.now(PARK_TZ).date(),
    now: Callable[[], datetime] = lambda: datetime.now(PARK_TZ),
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ElicitNode:
    """Node factory. The node never marks constraints valid: it only proposes them.

    Outputs:
    * missing information, an unreadable answer, a must-do/avoid name that is
      not exactly one catalog attraction, or one attraction that is both must-do
      and avoid -> ``constraints=None`` plus an AI
      question (its ``additional_kwargs["missing_information"]`` lists the keys);
      the question is the fixed text, reworded by ``asker`` when there is one;
    * otherwise ``constraints``, ``guest_profiles``, ``constraints_valid=False``
      and ``pending_hard_constraint_confirmation``.
    """

    def elicit(state: ParkMindState) -> dict[str, Any]:
        session_id = state["thread_id"]
        texts = human_texts(state)
        if not texts:
            raise ValueError("elicit needs at least one guest message")
        lang = detect_language(
            texts[-1], fallback=state.get("language") or DEFAULT_LANGUAGE
        )

        # Anything staged by an earlier pass is stale: this extraction replaces it.
        intake.discard(session_id)

        try:
            with bound_to_thread(session_id):
                outcome = extract_with_recovery(
                    extractor, texts, max_attempts=max_attempts
                )
        except LlmBudgetExhaustedError:
            # No model left for this thread: ask again, deterministically [G12].
            outcome = ExtractionOutcome(None, 0)
        except ExtractorUnavailableError:
            logger.exception("the extractor is unavailable")
            return _unavailable("assistant_unavailable", lang)
        outcome = _with_clock_answer(outcome, state, texts[-1])
        clock_answer = parse_clock(texts[-1]) if _asked_for_departure(state) else None
        outcome, walking_answer = _with_walking_answer(outcome, state, texts[-1])
        resolution = NameResolution()
        if outcome.extraction is None:
            missing = ["unreadable_response"]
        elif oversized := oversized_party(outcome.extraction):
            missing = oversized
        else:
            try:
                resolution = names.resolve(
                    [*outcome.extraction.must_do, *outcome.extraction.avoid]
                )
            except PlanningUnavailableError:
                logger.exception("attraction names cannot be resolved")
                return _unavailable("unavailable", lang)
            unverified_times = ungrounded_times(
                outcome.extraction,
                texts,
                clock_answer=clock_answer,
                walking_answer=walking_answer,
            )
            missing = (
                missing_information(outcome.extraction)
                + ungrounded_heights(outcome.extraction, texts)
                + unverified_times
                + (
                    []
                    if unverified_times
                    else lunch_after_departure(outcome.extraction)
                )
                + unresolved_names(resolution)
                + conflicting_names(outcome.extraction, resolution)
                + (
                    []
                    if _asked_for_heights(state)
                    else heights_needed(outcome.extraction, resolution)
                )
            )
        if outcome.extraction is None or missing:
            fixed = _question_for(
                missing,
                resolution,
                lang,
                len(outcome.extraction.guests) if outcome.extraction else 0,
                [g.label for g in outcome.extraction.guests]
                if outcome.extraction
                else (),
            )
            plain = plain_question(
                missing[0] if missing else None,
                fixed,
                _turns(state),
                lang,
                solo=bool(outcome.extraction and len(outcome.extraction.guests) == 1),
            )
            if asker is not None:
                with bound_to_thread(session_id):
                    fixed = word_question(
                        asker,
                        fixed=fixed,
                        turns=_turns(state),
                        language=lang,
                        fallback=plain,
                    )
            else:
                fixed = plain
            return {
                "language": lang,
                "constraints": None,
                "constraints_valid": False,
                "pending_hard_constraint_confirmation": None,
                "messages": [
                    AIMessage(
                        content=fixed,
                        additional_kwargs={"missing_information": missing},
                    )
                ],
            }

        extraction = outcome.extraction
        for staged in _staged(extraction):
            intake.stage(session_id, staged)
        return {
            "language": lang,
            "constraints": build_party_constraints(
                extraction, today=today(), resolution=resolution
            ),
            "guest_profiles": build_guest_profiles(extraction, now=now()),
            "constraints_valid": False,
            "pending_hard_constraint_confirmation": pending_confirmations(
                extraction, resolution
            ),
        }

    return elicit
