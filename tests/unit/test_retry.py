"""Unit tests for the shared retry helper (services/clients/_retry.py).

Exercises `send_with_retry` directly against a fake `send` callable and an
injected `sleep` -- no httpx.Client, no MockTransport, no real sleeping.
"""

import httpx
import pytest

from parkmind.services.clients import _retry
from parkmind.services.clients._retry import (
    RETRYABLE_STATUS_CODES,
    RetriesExhausted,
    RetryPolicy,
    send_with_retry,
)


def test_retryable_status_codes_are_the_historical_set():
    assert RETRYABLE_STATUS_CODES == {429, 500, 502, 503, 504}


def test_default_policy_matches_the_historical_defaults():
    policy = RetryPolicy()
    assert policy.max_attempts == 3
    assert policy.backoff_seconds == 0.5


def test_max_attempts_below_one_rejected_at_construction():
    with pytest.raises(ValueError, match="max_retries"):
        RetryPolicy(max_attempts=0)


def test_returns_the_first_response_without_retrying():
    calls = {"count": 0}

    def send() -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200)

    response = send_with_retry(send, policy=RetryPolicy(), sleep=_unexpected_sleep)

    assert response.status_code == 200
    assert calls["count"] == 1


@pytest.mark.parametrize("status_code", [404, 400, 301, 302])
def test_non_retryable_status_returned_without_retrying(status_code: int):
    calls = {"count": 0}

    def send() -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(status_code)

    response = send_with_retry(send, policy=RetryPolicy(), sleep=_unexpected_sleep)

    assert response.status_code == status_code
    assert calls["count"] == 1


def test_single_attempt_policy_never_retries_or_sleeps():
    calls = {"count": 0}

    def send() -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503)

    response = send_with_retry(
        send, policy=RetryPolicy(max_attempts=1), sleep=_unexpected_sleep
    )

    assert response.status_code == 503
    assert calls["count"] == 1


def test_backoff_is_exponential_and_only_between_attempts():
    """3 attempts, 0.5s base -> 0.5s then 1.0s: identical to the linear
    formula this module replaces at the historical default attempt count."""
    sleeps: list[float] = []
    calls = {"count": 0}

    def send() -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503)

    response = send_with_retry(
        send,
        policy=RetryPolicy(max_attempts=3, backoff_seconds=0.5),
        sleep=sleeps.append,
    )

    assert calls["count"] == 3
    assert sleeps == [0.5, 1.0]
    # Exhausted on a retryable status, not a transport error: the last
    # response comes back for the caller to turn into its own exception.
    assert response.status_code == 503


def test_backoff_diverges_from_linear_only_at_a_fourth_attempt():
    sleeps: list[float] = []

    def send() -> httpx.Response:
        return httpx.Response(503)

    send_with_retry(
        send,
        policy=RetryPolicy(max_attempts=4, backoff_seconds=0.5),
        sleep=sleeps.append,
    )

    assert sleeps == [0.5, 1.0, 2.0]  # linear would have been [0.5, 1.0, 1.5]


def test_recovers_after_transient_failures_within_the_attempt_budget():
    attempts = [
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("boom"),
        httpx.Response(200),
    ]
    sleeps: list[float] = []

    def send() -> httpx.Response:
        outcome = attempts.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    response = send_with_retry(send, policy=RetryPolicy(max_attempts=3), sleep=sleeps.append)

    assert response.status_code == 200
    assert sleeps == [0.5, 1.0]


def test_transport_errors_exhausted_raise_retries_exhausted():
    calls = {"count": 0}
    last_exc = httpx.ConnectError("boom")

    def send() -> httpx.Response:
        calls["count"] += 1
        raise last_exc

    with pytest.raises(RetriesExhausted) as exc_info:
        send_with_retry(send, policy=RetryPolicy(max_attempts=3), sleep=lambda _: None)

    assert calls["count"] == 3
    assert exc_info.value.attempts == 3
    assert exc_info.value.last_error is last_exc


def test_a_mix_of_retryable_status_and_transport_error_still_raises_on_exhaustion():
    outcomes = [httpx.Response(503), httpx.TimeoutException("boom")]

    def send() -> httpx.Response:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with pytest.raises(RetriesExhausted) as exc_info:
        send_with_retry(send, policy=RetryPolicy(max_attempts=2), sleep=lambda _: None)

    assert exc_info.value.attempts == 2


def test_default_sleep_is_looked_up_fresh_not_bound_at_definition_time(monkeypatch):
    """Regression: `sleep: Callable = time.sleep` as a default-argument value
    would bind the real `time.sleep` once, at import time, so a caller that
    never passes `sleep=` explicitly (every real adapter) would keep sleeping
    for real even after a test monkeypatches `_retry.time.sleep` -- exactly
    the case ThemeParksClient/OpenMeteoClient are in, since neither passes
    `sleep=` through `_request`."""
    sleeps: list[float] = []
    monkeypatch.setattr(_retry.time, "sleep", sleeps.append)

    def send() -> httpx.Response:
        return httpx.Response(503)

    send_with_retry(send, policy=RetryPolicy(max_attempts=2, backoff_seconds=0.5))

    assert sleeps == [0.5]


def _unexpected_sleep(_seconds: float) -> None:
    raise AssertionError("sleep should not be called when there is nothing to retry")
