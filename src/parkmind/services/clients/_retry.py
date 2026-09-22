"""Shared retry/backoff mechanics for services/clients HTTP adapters.

Extracted from ThemeParksClient (#55) and OpenMeteoClient (#54), which had
copied the same loop -- and, before self-review, the same two bugs (see
issue #56). Every future HTTP adapter (P0-09 routing, P1-06 Queue-Times)
should call `send_with_retry` instead of writing a new loop; the guard test
`tests/architecture/test_retry_not_duplicated.py` fails the build if one
shows up anyway.

Scope, deliberately narrow: this module owns the loop, the sleep, and which
HTTP statuses are worth retrying (`RETRYABLE_STATUS_CODES`). It does NOT own
exception types or messages -- those are legitimately adapter-specific
(`ThemeParksNotFoundError` vs `OpenMeteoNotFoundError`, Open-Meteo's own
`{"error": true, "reason": ...}` body parsing) and stay in each client's
`_request`. `send_with_retry` returns the final `httpx.Response` for every
case except a network-level failure that survived every attempt -- 2xx, a
redirect, a non-retryable 4xx, and a retryable status that was never
recovered are all handed back for the caller to interpret.

Backoff is exponential, `backoff_seconds * 2 ** (attempt - 1)`, slept only
BETWEEN attempts. At the historical default (3 attempts, 0.5s) this gives
the same two delays as the linear formula it replaces -- 0.5s, then 1.0s --
so migrating ThemeParksClient and OpenMeteoClient onto this module changed
no observable behavior. It only diverges from linear at a 4th attempt (2.0s
vs 1.5s), which neither adapter uses today.

No jitter, no `Retry-After` handling. Checked both providers' docs while
extracting this (https://api.themeparks.wiki/docs/v1,
https://open-meteo.com/en/docs, 2026-09-22): neither documents rate
limiting, HTTP 429, or a `Retry-After` header. Jitter has no demonstrated
benefit for a single in-process client without a real thundering-herd
scenario. Revisit both only with evidence -- a provider observed sending
`Retry-After`, or `max_attempts` raised enough that synchronized retries
become a real risk.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """`max_attempts` is the TOTAL number of attempts (>= 1), not the number
    of retries after the first -- matches every adapter's existing
    `max_retries` constructor parameter, name and meaning both."""

    max_attempts: int = 3
    backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_retries must be >= 1, got {self.max_attempts}")


class RetriesExhausted(Exception):
    """Every attempt failed at the transport level (timeout or connection
    error) -- there is no `httpx.Response` to hand back to the caller."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        super().__init__(f"failed after {attempts} attempts: {last_error}")
        self.attempts = attempts
        self.last_error = last_error


def send_with_retry(
    send: Callable[[], httpx.Response],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], None] | None = None,
) -> httpx.Response:
    """Call ``send()`` up to ``policy.max_attempts`` times.

    Retries on `httpx.TimeoutException`/`httpx.TransportError` and on a
    response whose status is in `RETRYABLE_STATUS_CODES`. Returns the first
    response that is NOT a retryable status -- 2xx, a redirect, a
    non-retryable 4xx -- without retrying it at all. If every attempt's
    response was a retryable status, returns the LAST one instead of
    raising, so the caller (which owns the exception taxonomy) decides what
    it means. Raises `RetriesExhausted` only when every attempt failed at
    the transport level, since there is no response in that case.

    ``sleep`` defaults to `time.sleep`, looked up fresh on every call (not
    bound as a default-argument value) so tests can monkeypatch this
    module's `time.sleep` even for callers that never pass `sleep=`
    explicitly -- `is None`, not a bound default, for the same reason
    `attraction_metadata` elsewhere in this codebase checks `is None`.
    """
    # `is None`, not a bound default: a default argument value is captured
    # once at function-definition time, so `time.sleep` would be frozen
    # before any test could monkeypatch it.
    _sleep = sleep if sleep is not None else time.sleep

    for attempt in range(1, policy.max_attempts + 1):
        is_last_attempt = attempt == policy.max_attempts

        try:
            response = send()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if is_last_attempt:
                raise RetriesExhausted(policy.max_attempts, exc) from exc
            _sleep(policy.backoff_seconds * 2 ** (attempt - 1))
            continue

        if is_last_attempt or response.status_code not in RETRYABLE_STATUS_CODES:
            return response

        _sleep(policy.backoff_seconds * 2 ** (attempt - 1))

    raise AssertionError("unreachable")  # loop always returns or raises
