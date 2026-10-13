"""Tool-calling extractor on the project's NVIDIA-hosted model (``get_llm``)."""

import os
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_nvidia_ai_endpoints import ChatNVIDIA

from parkmind.agents.elicit_agent import ExtractorOutputError
from parkmind.agents.elicit_prompt import ELICIT_SYSTEM_PROMPT
from parkmind.agents.elicit_schema import ElicitationExtraction
from parkmind.agents.llm_retry import RetryLog, call_with_retries


class NvidiaExtractor:
    """The client is built on first use, so importing this needs no API key."""

    def __init__(self) -> None:
        self._llm: Any = None

    def _runnable(self) -> Any:
        if self._llm is None:
            # Thinking is off: a reasoning model spends minutes on a request-sized extraction.
            self._llm = ChatNVIDIA(
                model=os.environ["PARKMIND_LLM_MODEL"],
                api_key=os.environ["NVIDIA_API_KEY"],
                temperature=0,
                max_tokens=2048,
                chat_template_kwargs={"enable_thinking": False},
                timeout=60,
            ).bind_tools(
                [ElicitationExtraction], tool_choice=ElicitationExtraction.__name__
            )
        return self._llm

    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(human_messages, start=1))
        content = f"Guest messages, oldest first:\n{listing}"
        if repair_hint:
            content += (
                "\n\nYour previous output was rejected by validation. Fix exactly these "
                f"problems and return the full corrected output:\n{repair_hint}"
            )
        messages = [SystemMessage(ELICIT_SYSTEM_PROMPT), HumanMessage(content)]
        llm = self._runnable()
        response = call_with_retries(lambda: llm.invoke(messages), log=RetryLog())
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            raise ExtractorOutputError("the model returned no structured output")
        args = calls[0].get("args")
        if not isinstance(args, Mapping):
            raise ExtractorOutputError("the model returned malformed structured output")
        return _without_empty_accessibility(args)


_ACCESS_FLAGS = (
    "daily_walking_limit_minutes",
    "rest_frequency_minutes",
    "mobility_requirements",
    "heat_sensitivity",
    "ride_restrictions",
)


def _without_empty_accessibility(args: Mapping[str, Any]) -> Mapping[str, Any]:
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
