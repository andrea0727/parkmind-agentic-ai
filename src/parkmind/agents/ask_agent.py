"""The ``ask`` step: words the next question to the guests so the chat reads naturally.

What is missing, and when to ask, stay deterministic (``elicit_agent``). This step
only rewords the fixed question using the conversation so far. Whatever it returns
is checked before it reaches the guests; when the model is unreachable, out of
budget or its answer fails a check, the fixed question is used.
"""

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from parkmind.agents.ask_prompt import ASK_PROMPT_VERSION, ASK_SYSTEM_PROMPT
from parkmind.agents.ask_schema import QuestionDraft
from parkmind.agents.concierge_prompt import PAYLOAD, as_data
from parkmind.agents.grounding import ungrounded_numerals
from parkmind.agents.llm_budget import (
    LlmBudgetExhaustedError,
    LlmCallBudget,
    invoke_counted,
)
from parkmind.agents.llm_client import build_chat_model
from parkmind.config.settings import settings
from parkmind.core.language import Language

ASK_BUDGET = LlmCallBudget(settings.ASK_CALLS_PER_THREAD)

__all__ = [
    "ASK_PROMPT_VERSION",
    "ASK_TIMEOUT_SECONDS",
    "AnthropicQuestionWriter",
    "QuestionOutputError",
    "QuestionUnavailableError",
    "QuestionWriter",
    "Turn",
    "word_question",
]

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
MAX_TURNS = 8
MAX_QUESTIONS = 1
ASK_TIMEOUT_SECONDS = settings.ASK_TIMEOUT_SECONDS

Turn = tuple[str, str]
"""``("guest" | "assistant", text)``."""


class QuestionUnavailableError(RuntimeError):
    """No model can be called (no API key): the fixed question is used."""


class QuestionOutputError(ValueError):
    """The model returned no structured output."""


class QuestionWriter(Protocol):
    def write(
        self, request_json: str, repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        """Raw structured output for the request; validated by the caller."""
        ...


class AnthropicQuestionWriter:
    """Tool-calling question writer. The client is built on first use.

    Every request is bounded (``timeout``, no SDK retry): the guests are waiting
    for the next question and the fixed one can stand in.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = ASK_TIMEOUT_SECONDS,
        budget: LlmCallBudget | None = None,
    ) -> None:
        self._model = model or settings.ASK_MODEL
        self._api_key = api_key if api_key is not None else settings.LLM_API_KEY
        self._timeout = timeout
        self._budget = budget or ASK_BUDGET
        self._llm: Any = None

    def _runnable(self) -> Any:
        if not self._api_key:
            raise QuestionUnavailableError(
                f"no {settings.LLM_API_KEY_ENV} is configured"
            )
        if self._llm is None:
            chat = build_chat_model(
                model=self._model,
                api_key=self._api_key,
                temperature=0.4,
                max_tokens=400,
                timeout=self._timeout,
            )
            self._llm = chat.bind_tools(
                [QuestionDraft], tool_choice=QuestionDraft.__name__
            )
        return self._llm

    def write(
        self, request_json: str, repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        content = f"Request:\n{as_data(PAYLOAD, request_json)}"
        if repair_hint:
            content += (
                "\n\nYour previous message was rejected. Fix exactly this and "
                f"return the full corrected message:\n{repair_hint}"
            )
        response = invoke_counted(
            self._runnable(),
            [SystemMessage(ASK_SYSTEM_PROMPT), HumanMessage(content)],
            retries=0,
            budget=self._budget,
        )
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            raise QuestionOutputError("the model returned no structured output")
        args = calls[0].get("args")
        if not isinstance(args, Mapping):
            raise QuestionOutputError("the model returned malformed structured output")
        return args


def _same_words(a: str, b: str) -> bool:
    def norm(text: str) -> str:
        return " ".join(re.findall(r"\w+", text.casefold()))

    return norm(a) == norm(b)


ECHO_WORDS = 4
UNSURE = re.compile(
    r"\b(no s[eé]|ni idea|no estoy segur[oa]s?|not sure|unsure|don'?t know|no idea)\b",
    re.IGNORECASE,
)
OPTIONS = re.compile(r"\b(o|or)\b", re.IGNORECASE)
APPROXIMATELY = re.compile(r"\b(m[aá]s o menos|more or less|o sea)\b", re.IGNORECASE)


MEASURE = re.compile(
    r"\b(min|minut\w*|hour\w*|hora\w*|cm|metros?|meters?|feet|pies?|\w*dólar\w*|dollars?|euros?)\b|[$€]",
    re.IGNORECASE,
)
MAX_HEAD_COUNT = 10


def _small_head_count(figure: str) -> bool:
    """A plain small whole number, which unsure guests may be offered as an answer to \"how many are you\"."""
    return figure.isdigit() and 0 < int(figure) <= MAX_HEAD_COUNT


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _echoes(text: str, said: str | None, fixed: str) -> bool:
    """True when ``text`` copies a run of the guests' last words that ``fixed`` does not need."""
    if said is None:
        return False
    runs = lambda words: {
        tuple(words[i : i + ECHO_WORDS]) for i in range(len(words) - ECHO_WORDS + 1)
    }
    return bool((runs(_words(said)) & runs(_words(text))) - runs(_words(fixed)))


GREETINGS = frozenset(
    [
        "hello",
        "gracias",
        "thanks",
        "perfecto",
        "perfect",
        "genial",
        "great",
        "entendido",
        "understood",
        "claro",
        "sure",
    ]
)
MIN_FACT_CHARS = 5


def _restates_facts(text: str, said: str | None, fixed: str) -> bool:
    """True when the message repeats a figure or word the guests just wrote."""
    if said is None:
        return False

    def facts(body: str) -> set[str]:
        return {
            word
            for word in _words(body)
            if (word.isdigit() or len(word) >= MIN_FACT_CHARS) and word not in GREETINGS
        }

    return bool((facts(text) & facts(said)) - facts(fixed))


def _problem(
    text: str,
    *,
    fixed: str,
    known: str,
    language: Language,
    previous: str | None,
    said: str | None,
) -> str | None:
    if "?" not in text and "¿" not in text:
        return "the message must end by asking the guests the question"
    if text.count("?") > MAX_QUESTIONS:
        return "ask exactly one short question, with a single question mark"
    if previous is not None and _same_words(text, previous):
        return "this repeats the previous question word for word; answer what the guests just said and ask it differently"
    if _echoes(text, said, fixed) or _restates_facts(text, said, fixed):
        return "this repeats back what the guests just wrote; acknowledge it in a few words of your own and ask the question"
    invented = ungrounded_numerals(text, f"{fixed}\n{known}", language)
    if said is not None and UNSURE.search(said) and not MEASURE.search(text):
        invented = [n for n in invented if not _small_head_count(n)]
    if invented:
        return f"these figures are not in must_ask or the guests' messages: {', '.join(invented)}"
    if (
        said is not None
        and UNSURE.search(said)
        and not OPTIONS.search(APPROXIMATELY.sub("", text))
    ):
        return "the guests are unsure: offer two or three options in words, joined with 'or' (in Spanish 'o')"
    return None


def word_question(
    writer: QuestionWriter,
    *,
    fixed: str,
    turns: Sequence[Turn],
    language: Language,
    fallback: str | None = None,
) -> str:
    """The question in natural words, or ``fallback`` (default ``fixed``) when the model cannot give a checked one."""
    stand_in = fixed if fallback is None else fallback
    recent = list(turns)[-MAX_TURNS:]
    previous = next(
        (text for role, text in reversed(recent) if role == "assistant"), None
    )
    said = next((text for role, text in reversed(recent) if role == "guest"), None)
    known = "\n".join(text for role, text in recent if role == "guest")
    request = json.dumps(
        {
            "language": language,
            "conversation": [{"role": role, "text": text} for role, text in recent],
            "must_ask": fixed,
            "previous_question": previous,
        },
        ensure_ascii=False,
    )
    hint: str | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            draft = QuestionDraft.model_validate(writer.write(request, hint))
        except (QuestionUnavailableError, LlmBudgetExhaustedError):
            return stand_in
        except (QuestionOutputError, ValidationError):
            hint = "return one message of at most 600 characters"
            continue
        except Exception:
            logger.exception("the question writer failed")
            return stand_in
        text = " ".join(draft.text.split())
        problem = _problem(
            text,
            fixed=fixed,
            known=known,
            language=language,
            previous=previous,
            said=said,
        )
        if problem is None:
            return text
        hint = f"{problem}\n\nYour previous message:\n{text}"
    return stand_in
