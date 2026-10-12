import pytest

from parkmind.core.llm_retry import RetryLog, call_with_retries, is_transient_error


class Timeout(Exception):
    """Same name as requests.exceptions.Timeout."""


@pytest.mark.parametrize(
    "exc",
    [
        Exception("[500] Internal Server Error\nRequestID: x"),
        Exception("[502] Bad Gateway"),
        Exception("[503] Service Unavailable"),
        Exception("[504] Gateway Time-out"),
        Exception("[429] Too Many Requests"),
        Exception("Internal server error"),
        TimeoutError("read timed out"),
        ConnectionResetError("reset"),
        Timeout("slow"),
    ],
)
def test_transient_errors_are_recognised(exc: Exception) -> None:
    assert is_transient_error(exc)


@pytest.mark.parametrize(
    "exc",
    [
        Exception("[401] Unauthorized\nPlease check or regenerate your API key."),
        Exception("[400] Bad Request"),
        Exception("[404] Not Found"),
        Exception("[422] Unprocessable"),
        ValueError("party_size must be positive"),
        Exception("the answer was 500 words long"),
    ],
)
def test_other_errors_are_not(exc: Exception) -> None:
    assert not is_transient_error(exc)


def _flaky(failures: list[Exception], result: str = "ok"):
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        if failures:
            raise failures.pop(0)
        return result

    return fn, calls


def test_a_call_that_works_is_made_once() -> None:
    fn, calls = _flaky([])
    log = RetryLog()

    assert call_with_retries(fn, log=log, sleep=lambda _s: None) == "ok"
    assert calls["n"] == 1
    assert log.retries == 0
    assert log.errors == []


def test_a_transient_error_is_retried_until_it_works() -> None:
    fn, calls = _flaky([Exception("[500] Internal Server Error"), TimeoutError("t")])
    log = RetryLog()

    assert call_with_retries(fn, log=log, sleep=lambda _s: None) == "ok"
    assert calls["n"] == 3
    assert log.retries == 2
    assert log.errors[0].startswith("Exception: [500]")
    assert log.errors[1].startswith("TimeoutError")


def test_the_last_error_is_raised_when_attempts_run_out() -> None:
    fn, calls = _flaky([Exception("[500] a")] * 5)

    with pytest.raises(Exception, match=r"\[500\] a"):
        call_with_retries(fn, attempts=3, sleep=lambda _s: None)

    assert calls["n"] == 3


def test_a_client_error_is_raised_at_once() -> None:
    fn, calls = _flaky([Exception("[401] Unauthorized")])

    with pytest.raises(Exception, match=r"\[401\]"):
        call_with_retries(fn, sleep=lambda _s: None)

    assert calls["n"] == 1


def test_the_wait_doubles_and_is_capped() -> None:
    fn, _ = _flaky([Exception("[500] x")] * 5)
    waits: list[float] = []

    call_with_retries(fn, attempts=6, base_delay=2, max_delay=10, sleep=waits.append)

    assert waits == [2, 4, 8, 10, 10]


def test_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError):
        call_with_retries(lambda: 1, attempts=0)
