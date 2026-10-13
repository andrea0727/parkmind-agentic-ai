"""``explain``: a validated plan -> words for the guests (section 40, P0-31).

The LLM only phrases; it never decides. It receives the payload that
``ExplainPlanUseCase.prepare`` builds -- validated plan, diff, provenance and the
preference weights, labelled stated or learned -- and returns an
``ExplanationDraft``. Its text reaches the guests only if every numeral in it is in
the payload (``agents/grounding.py``).

``explain_grounded`` is section 40's safety path [C17]: at most two calls -- a
schema failure, an ungrounded numeral or a preference figure without its right
stated/learned label gets one retry, with the errors listed -- then the
deterministic template. A narrator that cannot be
called (no key, a network error, anything) goes straight to the template, so
EXPLAIN never fails a plan. Logs carry counts and error types, never the payload
or the model's text [C19].
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr, ValidationError

from parkmind.agents.explain_prompt import EXPLAIN_PROMPT_VERSION, EXPLAIN_SYSTEM_PROMPT
from parkmind.agents.explain_schema import ExplanationDraft
from parkmind.agents.grounding import (
    mislabelled_preferences,
    numerals,
    ungrounded_numerals,
)
from parkmind.config.settings import settings
from parkmind.services.use_cases.explain_plan import (
    ExplanationPayload,
    PreparedExplanation,
)

__all__ = [
    "EXPLAIN_SDK_RETRIES",
    "EXPLAIN_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
    "TEMPLATE_VERSION",
    "AnthropicNarrator",
    "GroundedExplanation",
    "NarratorOutputError",
    "NarratorUnavailableError",
    "PlanNarrator",
    "explain_grounded",
]

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
"""One call and one retry (section 40)."""
TEMPLATE_VERSION = "template"
"""``Provenance.prompt_versions["explain"]`` when the template was used."""
EXPLAIN_TIMEOUT_SECONDS = 20.0
"""Per request. EXPLAIN is on the plan's path: a hung call must end in the template."""
EXPLAIN_SDK_RETRIES = 1
"""The client's own retries per call, on top of ``MAX_ATTEMPTS``."""


class NarratorUnavailableError(RuntimeError):
    """No model can be called (no API key): the template is used, nothing is sent."""


class NarratorOutputError(ValueError):
    """The model returned no structured output."""


class PlanNarrator(Protocol):
    def narrate(
        self, payload_json: str, repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        """Raw structured output for the payload; validated by the caller."""
        ...


class AnthropicNarrator:
    """Claude tool-calling narrator. The client is built on first use.

    Unlike elicit's extractor, every request is bounded (``timeout``, a single SDK
    retry): the guests are waiting for their plan, and the template can stand in.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = EXPLAIN_TIMEOUT_SECONDS,
        max_retries: int = EXPLAIN_SDK_RETRIES,
    ) -> None:
        self._model = model or settings.EXPLAIN_MODEL
        self._api_key = api_key if api_key is not None else settings.ANTHROPIC_API_KEY
        self._timeout = timeout
        self._max_retries = max_retries
        self._llm: Any = None

    def _runnable(self) -> Any:
        if not self._api_key:
            raise NarratorUnavailableError("no ANTHROPIC_API_KEY is configured")
        if self._llm is None:
            from langchain_anthropic import ChatAnthropic

            chat = ChatAnthropic(
                model_name=self._model,
                api_key=SecretStr(self._api_key),
                temperature=0,
                max_tokens_to_sample=1500,
                timeout=self._timeout,
                max_retries=self._max_retries,
                stop=None,
            )
            self._llm = chat.bind_tools(
                [ExplanationDraft], tool_choice=ExplanationDraft.__name__
            )
        return self._llm

    def narrate(
        self, payload_json: str, repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        content = f"Payload:\n{payload_json}"
        if repair_hint:
            content += (
                "\n\nYour previous explanation was rejected. Fix exactly this and "
                f"return the full corrected explanation:\n{repair_hint}"
            )
        response = self._runnable().invoke(
            [SystemMessage(EXPLAIN_SYSTEM_PROMPT), HumanMessage(content)]
        )
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            raise NarratorOutputError("the model returned no structured output")
        args = calls[0].get("args")
        if not isinstance(args, Mapping):
            raise NarratorOutputError("the model returned malformed structured output")
        return args


@dataclass(frozen=True)
class GroundedExplanation:
    text: str
    source: Literal["llm", "template"]
    attempts: int
    """Calls made to the narrator (0 when it could not be called)."""
    prompt_version: str
    """``EXPLAIN_PROMPT_VERSION``, or ``TEMPLATE_VERSION`` for the fallback."""


def _template(prepared: PreparedExplanation, attempts: int) -> GroundedExplanation:
    return GroundedExplanation(
        prepared.template, "template", attempts, TEMPLATE_VERSION
    )


def _schema_errors(exc: ValidationError) -> str:
    """The fields that failed, without the values the model wrote."""
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'output'}: {err['msg']}"
        for err in exc.errors(include_input=False)
    )


def _with_previous(text: str, problem: str) -> str:
    """The repair hint: what was wrong, and the answer to fix (never logged)."""
    return f"{problem}\n\nYour previous explanation:\n{text}"


def _preference_labels(payload: ExplanationPayload) -> tuple[dict[str, str], set[str]]:
    """Each stated/learned weight's figure -> its source, and the figures too ambiguous
    to check: also a stop figure, or shared by weights of different sources."""
    others = numerals(replace(payload, preferences=[]).to_json())
    sources: dict[str, set[str]] = {}
    for signal in payload.preferences:
        for value in numerals(str(signal.value)):
            sources.setdefault(value, set()).add(signal.source)
    labelled: dict[str, str] = {}
    ambiguous: set[str] = set()
    for value, found in sources.items():
        if value in others or len(found) > 1:
            ambiguous.add(value)
        elif (source := next(iter(found))) in ("stated", "learned"):
            labelled[value] = source
    return labelled, ambiguous


def explain_grounded(
    narrator: PlanNarrator | None, prepared: PreparedExplanation
) -> GroundedExplanation:
    """The narrator's explanation if it is grounded, else the template."""
    if narrator is None:
        return _template(prepared, 0)
    payload_json = prepared.payload.to_json()
    labelled, ambiguous = _preference_labels(prepared.payload)
    hint: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = narrator.narrate(payload_json, hint)
        except NarratorUnavailableError:
            logger.info("explain: no narrator available, using the template")
            return _template(prepared, attempt - 1)
        except Exception as exc:  # noqa: BLE001 - any failure falls back, never breaks the plan
            logger.warning(
                "explain: the narrator failed (%s), using the template",
                type(exc).__name__,
            )
            return _template(prepared, attempt)
        try:
            draft = ExplanationDraft.model_validate(raw)
        except ValidationError as exc:
            hint = f"The output did not match the schema: {_schema_errors(exc)}"
            logger.warning("explain: attempt %d did not match the schema", attempt)
            continue
        ungrounded = ungrounded_numerals(draft.text, payload_json)
        if ungrounded:
            hint = _with_previous(
                draft.text,
                "These numbers are not in the payload: "
                + ", ".join(ungrounded)
                + ". Use only figures that appear in the payload, exactly as given.",
            )
            logger.warning(
                "explain: attempt %d had %d ungrounded numeral(s)",
                attempt,
                len(ungrounded),
            )
            continue
        mislabelled = mislabelled_preferences(draft.text, labelled, ambiguous)
        if mislabelled:
            hint = _with_previous(
                draft.text,
                "Label each preference figure by where it comes from: "
                + "; ".join(mislabelled)
                + ".",
            )
            logger.warning(
                "explain: attempt %d had %d mislabelled preference figure(s)",
                attempt,
                len(mislabelled),
            )
            continue
        return GroundedExplanation(draft.text, "llm", attempt, EXPLAIN_PROMPT_VERSION)
    return _template(prepared, MAX_ATTEMPTS)
