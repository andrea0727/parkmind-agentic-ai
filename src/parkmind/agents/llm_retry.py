"""Retry a call on transient server-side failures (5xx, 429, timeouts, dropped connections).

Client errors (4xx other than 429: bad key, bad request) are never retried: repeating
them cannot succeed and would only hide the cause.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")

_TRANSIENT_STATUS = re.compile(r"\b(?:5\d\d|429)\b")
_TRANSIENT_PHRASES = (
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway time-out",
    "gateway timeout",
    "too many requests",
    "overloaded",
)
_TRANSIENT_TYPES = frozenset(
    {"Timeout", "ReadTimeout", "ConnectTimeout", "ConnectionError", "ChunkedEncodingError"}
)


def is_transient_error(exc: BaseException) -> bool:
    """True for failures worth retrying.

    The NVIDIA client raises a bare ``Exception("[500] Internal Server Error ...")`` for
    HTTP errors, so the status is read from the message; network failures are told apart
    by their exception types.
    """
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if any(cls.__name__ in _TRANSIENT_TYPES for cls in type(exc).__mro__):
        return True
    message = str(exc)
    header = message.split("\n", 1)[0]
    if header.startswith("[") and _TRANSIENT_STATUS.search(header.split("]", 1)[0]):
        return True
    lowered = message.lower()
    return any(phrase in lowered for phrase in _TRANSIENT_PHRASES)


@dataclass
class RetryLog:
    """What a retried call went through, for reports."""

    attempts: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def retries(self) -> int:
        return max(self.attempts - 1, 0)


def call_with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    log: RetryLog | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn``; on a transient error wait ``base_delay * 2**n`` (capped) and try again.

    The last error is raised once ``attempts`` are used up; a non-transient error is
    raised at once.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    log = log if log is not None else RetryLog()
    for attempt in range(1, attempts + 1):
        log.attempts += 1
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_transient_error(exc):
                raise
            log.errors.append(f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
            sleep(min(base_delay * 2 ** (attempt - 1), max_delay))
    raise AssertionError("unreachable")
