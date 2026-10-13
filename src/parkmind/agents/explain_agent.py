"""``explain``: a validated plan -> words for the guests (section 40, P0-31).

The LLM only phrases; it never decides. It receives the payload that
``ExplainPlanUseCase.prepare`` builds -- validated plan, diff, provenance and the
preference weights, labelled stated or learned -- and returns an
``ExplanationDraft``. Its text reaches the guests only if every numeral in it is in
the payload (``agents/grounding.py``).
"""

from collections.abc import Mapping
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr

from parkmind.agents.explain_prompt import EXPLAIN_SYSTEM_PROMPT
from parkmind.agents.explain_schema import ExplanationDraft
from parkmind.config.settings import settings

__all__ = [
    "AnthropicNarrator",
    "NarratorOutputError",
    "NarratorUnavailableError",
    "PlanNarrator",
]


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
    """Claude tool-calling narrator. The client is built on first use."""

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self._model = model or settings.EXPLAIN_MODEL
        self._api_key = api_key if api_key is not None else settings.ANTHROPIC_API_KEY
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
                timeout=None,
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
