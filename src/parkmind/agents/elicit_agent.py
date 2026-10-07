"""``elicit``: natural language -> structured guest and planning information.

The LLM only classifies and extracts into ``ElicitationExtraction``. This
module then turns the validated extraction into contracts and decides what is
still missing. It never decides what the checker enforces:

* soft preferences become ``GuestProfile`` values (stated, tradeable);
* every hard item -- including all accessibility flags -- is only *pending*:
  it is listed in ``pending_hard_constraint_confirmation`` and the graph
  confirms it with the human before ``constraints_valid`` can become True.
* accessibility flags are staged through ``AccessibilityIntakeUseCase``, never
  written to state, and never stored as soft preferences [C15, C19].
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Protocol

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import SecretStr, ValidationError

from parkmind.agents.elicit_prompt import ELICIT_PROMPT_VERSION, ELICIT_SYSTEM_PROMPT
from parkmind.agents.elicit_schema import ElicitationExtraction, normalize_name
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
from parkmind.graph.state import ParkMindState
from parkmind.services.use_cases.accessibility_intake import (
    AccessibilityIntakeUseCase,
    StagedAccessibility,
)
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
    "GuestInfoExtractor",
    "build_guest_profiles",
    "build_party_constraints",
    "echo_for",
    "extract_with_recovery",
    "human_texts",
    "make_elicit_node",
    "missing_information",
    "pending_confirmations",
    "unresolved_names",
]

DEFAULT_MAX_ATTEMPTS = 2
_DEFAULT_TOLERANCE = 0.5
_STATED_CONFIDENCE = 0.8

_MISSING_QUESTIONS = {
    "guests": "Who is in your party (how many people, and are they adults or children)?",
    "departure_time": "What time do you plan to leave the park?",
    "unreadable_response": "I couldn't understand that. Could you describe your party and plans again?",
}


class ElicitNode(Protocol):
    def __call__(self, state: ParkMindState) -> dict[str, Any]: ...


class ExtractorOutputError(ValueError):
    """The extractor returned no usable structured output (retried like a schema failure)."""


class GuestInfoExtractor(Protocol):
    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        """Raw structured output for the guests' messages; validated by the caller."""
        ...


class AnthropicExtractor:
    """Claude tool-calling extractor. The client is built on first use."""

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self._model = model or settings.ELICIT_MODEL
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
        self._llm: Any = None

    def _runnable(self) -> Any:
        if self._llm is None:
            from langchain_anthropic import ChatAnthropic

            chat = ChatAnthropic(
                model_name=self._model,
                api_key=SecretStr(self._api_key),
                temperature=0,
                max_tokens_to_sample=1500,
                timeout=None,
                stop=None,
            )
            self._llm = chat.bind_tools(
                [ElicitationExtraction], tool_choice=ElicitationExtraction.__name__
            )
        return self._llm

    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        listing = "\n".join(f"{i}. {text}" for i, text in enumerate(human_messages, start=1))
        content = f"Guest messages, oldest first:\n{listing}"
        if repair_hint:
            content += (
                "\n\nYour previous output was rejected by validation. Fix exactly these "
                f"problems and return the full corrected output:\n{repair_hint}"
            )
        response = self._runnable().invoke(
            [SystemMessage(ELICIT_SYSTEM_PROMPT), HumanMessage(content)]
        )
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            raise ExtractorOutputError("the model returned no structured output")
        args = calls[0].get("args")
        if not isinstance(args, Mapping):
            raise ExtractorOutputError("the model returned malformed structured output")
        return args


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


def unresolved_names(resolution: NameResolution) -> list[str]:
    """Missing-information keys for must-do/avoid names that match no single attraction."""
    return [f"{_UNKNOWN_ATTRACTION}{name}" for name in resolution.unknown] + [
        f"{_AMBIGUOUS_ATTRACTION}{name}" for name in resolution.ambiguous
    ]


def _question_for(missing: Sequence[str], resolution: NameResolution | None = None) -> str:
    questions: list[str] = []
    for key in missing:
        if key.startswith("guest_role:"):
            questions.append(f"Is {key.split(':', 1)[1]} an adult or a child?")
        elif key.startswith(_UNKNOWN_ATTRACTION):
            name = key.removeprefix(_UNKNOWN_ATTRACTION)
            questions.append(
                f"I couldn't find '{name}' among this park's attractions. Which attraction do you mean?"
            )
        elif key.startswith(_AMBIGUOUS_ATTRACTION):
            name = key.removeprefix(_AMBIGUOUS_ATTRACTION)
            options = resolution.ambiguous[name] if resolution is not None else ()
            questions.append(f"'{name}' could be {', '.join(options)}. Which one do you mean?")
        else:
            questions.append(_MISSING_QUESTIONS.get(key, f"Could you tell me about {key}?"))
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
        Guest(guest_id=f"g{i}", role=g.role, height_cm=g.height_cm)  # type: ignore[arg-type]
        for i, g in enumerate(extraction.guests, start=1)
    ]
    window = extraction.lunch_window
    assert extraction.departure_time is not None
    return PartyConstraints(
        party_size=extraction.party_size or len(guests),
        guests=guests,
        must_do=resolution.node_ids(extraction.must_do),
        avoid=resolution.node_ids(extraction.avoid),
        lunch_window=TimeWindow(start=_at(today, window.start), end=_at(today, window.end))
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
        value=_DEFAULT_TOLERANCE, source=PreferenceSource.DEFAULT, confidence=0.0, updated_at=now
    )


def build_guest_profiles(extraction: ElicitationExtraction, *, now: datetime) -> list[GuestProfile]:
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


def pending_confirmations(extraction: ElicitationExtraction, resolution: NameResolution) -> list[str]:
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
        f"accessibility:g{ref}" for ref in sorted({a.guest_ref for a in extraction.accessibility})
    )
    return pending


def echo_for(entry: str) -> str:
    """Plain-language echo of a non-accessibility pending entry, for confirmation."""
    kind, _, value = entry.partition(": ")
    if kind == "must_do":
        return f"'{value}' is a must-do: the plan has to include it."
    if kind == "avoid":
        return f"'{value}' is off-limits: the plan must never include it."
    if kind == "departure_time":
        return f"You leave the park at {value}: the plan must end by then."
    if kind == "lunch_window":
        start, _, end = value.partition("-")
        return f"Lunch is fixed between {start} and {end}."
    if kind == "walking_budget":
        return f"The party's total walking must not exceed {value} minutes."
    return entry


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
            {"mobility": [], "restrictions": [], "limit": None, "rest": None, "heat": False},
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


def make_elicit_node(
    extractor: GuestInfoExtractor,
    intake: AccessibilityIntakeUseCase,
    names: AttractionNameResolver,
    *,
    today: Callable[[], date] = lambda: datetime.now(PARK_TZ).date(),
    now: Callable[[], datetime] = lambda: datetime.now(PARK_TZ),
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ElicitNode:
    """Node factory. The node never marks constraints valid: it only proposes them.

    Outputs:
    * missing information, an unreadable answer, or a must-do/avoid name that is
      not exactly one catalog attraction -> ``constraints=None`` plus an AI
      question (its ``additional_kwargs["missing_information"]`` lists the keys);
    * otherwise ``constraints``, ``guest_profiles``, ``constraints_valid=False``
      and ``pending_hard_constraint_confirmation``.
    """

    def elicit(state: ParkMindState) -> dict[str, Any]:
        session_id = state["thread_id"]
        texts = human_texts(state)
        if not texts:
            raise ValueError("elicit needs at least one guest message")

        # Anything staged by an earlier pass is stale: this extraction replaces it.
        intake.discard(session_id)

        outcome = extract_with_recovery(extractor, texts, max_attempts=max_attempts)
        resolution = NameResolution()
        if outcome.extraction is None:
            missing = ["unreadable_response"]
        else:
            resolution = names.resolve([*outcome.extraction.must_do, *outcome.extraction.avoid])
            missing = missing_information(outcome.extraction) + unresolved_names(resolution)
        if outcome.extraction is None or missing:
            return {
                "constraints": None,
                "constraints_valid": False,
                "pending_hard_constraint_confirmation": None,
                "messages": [
                    AIMessage(
                        content=_question_for(missing, resolution),
                        additional_kwargs={"missing_information": missing},
                    )
                ],
            }

        extraction = outcome.extraction
        for staged in _staged(extraction):
            intake.stage(session_id, staged)
        return {
            "constraints": build_party_constraints(
                extraction, today=today(), resolution=resolution
            ),
            "guest_profiles": build_guest_profiles(extraction, now=now()),
            "constraints_valid": False,
            "pending_hard_constraint_confirmation": pending_confirmations(extraction, resolution),
        }

    return elicit
