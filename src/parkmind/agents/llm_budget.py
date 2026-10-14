"""A budget of LLM calls per thread, counted where the calls are made [G12].

A thread is one guest conversation. Without a cap, one thread that keeps being
asked to rephrase (or that keeps failing validation) spends the LLM key without
bound: the API's daily caps count messages, not the calls a message can cause.

The clients count every call to the model, retries included, so they make their
own retries (the SDK's are switched off: it would retry out of sight). Once a
thread has spent its budget the clients raise ``LlmBudgetExhaustedError`` and
the agents take their deterministic path: elicit asks again, explain writes the
template. Only counts are logged, never a thread id or any text.

The thread is not an argument of the clients (the extractor and narrator
protocols are model-agnostic): the node binds it for the duration of its calls.
Calls made with no thread bound (evals, scripts) are not counted.
"""

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from parkmind.config.settings import settings

logger = logging.getLogger(__name__)

_MAX_THREADS_TRACKED = 10_000
_BACKOFF_SECONDS = 0.5
_MAX_BACKOFF_SECONDS = 4.0


class LlmBudgetExhaustedError(RuntimeError):
    """The thread has made every LLM call it is allowed: nothing was sent."""


class LlmCallBudget:
    """Calls made per thread, thread-safe. The oldest threads are forgotten past a cap."""

    def __init__(self, limit: int, *, max_threads: int = _MAX_THREADS_TRACKED) -> None:
        if limit < 1:
            raise ValueError("the LLM call budget must allow at least one call")
        self.limit = limit
        self._max_threads = max_threads
        self._used: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.Lock()

    def used(self, thread_id: str) -> int:
        with self._lock:
            return self._used.get(thread_id, 0)

    def acquire(self, thread_id: str) -> bool:
        """Count one call; False (and nothing counted) when the thread has none left."""
        with self._lock:
            used = self._used.get(thread_id, 0)
            if used >= self.limit:
                logger.warning(
                    "llm budget spent: %d of %d calls used on this thread",
                    used,
                    self.limit,
                )
                return False
            self._used[thread_id] = used + 1
            self._used.move_to_end(thread_id)
            while len(self._used) > self._max_threads:
                self._used.popitem(last=False)
            return True


DEFAULT_BUDGET = LlmCallBudget(settings.LLM_CALLS_PER_THREAD)

_current_thread: ContextVar[str | None] = ContextVar("llm_budget_thread", default=None)


@contextmanager
def bound_to_thread(thread_id: str | None) -> Iterator[None]:
    """Charge the LLM calls made inside to ``thread_id`` (``None``: not counted)."""
    token = _current_thread.set(thread_id)
    try:
        yield
    finally:
        _current_thread.reset(token)


def _retryable() -> tuple[type[Exception], ...]:
    """What the SDK itself used to retry: no connection, timeout, rate limit, server error."""
    import anthropic

    return (
        anthropic.APIConnectionError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
    )


def invoke_counted(
    runnable: Any,
    messages: Sequence[Any],
    *,
    retries: int,
    budget: LlmCallBudget | None = None,
) -> Any:
    """``runnable.invoke(messages)``, each attempt counted against the bound thread.

    Retries transient failures up to ``retries`` times, each one spending budget
    like a first call. Raises ``LlmBudgetExhaustedError`` before sending when
    the thread has none left.
    """
    budget = budget or DEFAULT_BUDGET
    attempts = max(0, retries) + 1
    for attempt in range(attempts):
        thread_id = _current_thread.get()
        if thread_id is not None and not budget.acquire(thread_id):
            raise LlmBudgetExhaustedError("the thread's LLM call budget is spent")
        try:
            return runnable.invoke(messages)
        except _retryable():
            if attempt == attempts - 1:
                raise
            time.sleep(min(_BACKOFF_SECONDS * 2**attempt, _MAX_BACKOFF_SECONDS))
    raise AssertionError("unreachable")  # pragma: no cover
