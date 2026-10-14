"""Fault injection around the in-memory ports: flaky, down, slow and corrupt."""

import sys
import threading
from collections.abc import Callable, Collection
from typing import Any, TypeVar

import pytest

T = TypeVar("T")

DEADLINE_SECONDS = 20.0


def run_with_deadline(call: Callable[[], T], seconds: float = DEADLINE_SECONDS) -> T:
    """Run ``call`` on a worker thread and fail the test if it has not returned in time."""
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = call()
        except BaseException as exc:  # noqa: BLE001
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        pytest.fail(f"no result within {seconds} seconds: the call is stuck")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


class FakeClock:
    """A clock that only moves when told to, so slow ports cost no real time."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Wrapper:
    """Forwards every attribute to ``inner``; methods named in ``methods`` get the fault."""

    def __init__(self, inner: Any, methods: Collection[str] | None) -> None:
        self._inner = inner
        self._methods = None if methods is None else frozenset(methods)
        self.calls: dict[str, int] = {}

    def _faulty(self, name: str) -> bool:
        return self._methods is None or name in self._methods

    def _intercept(self, name: str, target: Callable[..., Any]) -> Callable[..., Any]:
        raise NotImplementedError

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if name.startswith("_") or not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] = self.calls.get(name, 0) + 1
            if not self._faulty(name):
                return attribute(*args, **kwargs)
            return self._intercept(name, lambda: attribute(*args, **kwargs))

        return call

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())


class FlakyPort(_Wrapper):
    """Raises ``error`` on the first ``failures`` faulty calls, then delegates."""

    def __init__(
        self,
        inner: Any,
        error: Exception,
        *,
        failures: int = 1,
        methods: Collection[str] | None = None,
    ) -> None:
        super().__init__(inner, methods)
        self._error = error
        self.remaining_failures = failures
        self.failed = 0

    def _intercept(self, name: str, target: Callable[..., Any]) -> Any:
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            self.failed += 1
            raise self._error
        return target()

    def recover(self) -> None:
        self.remaining_failures = 0


class DownPort(FlakyPort):
    """Raises ``error`` on every faulty call until ``recover`` is called."""

    def __init__(
        self,
        inner: Any,
        error: Exception,
        *,
        methods: Collection[str] | None = None,
    ) -> None:
        super().__init__(inner, error, failures=sys.maxsize, methods=methods)


class SlowPort(_Wrapper):
    """Advances ``clock`` by ``delay`` seconds on each faulty call, then delegates."""

    def __init__(
        self,
        inner: Any,
        clock: FakeClock,
        delay: float,
        *,
        methods: Collection[str] | None = None,
    ) -> None:
        super().__init__(inner, methods)
        self._clock = clock
        self._delay = delay

    def _intercept(self, name: str, target: Callable[..., Any]) -> Any:
        self._clock.advance(self._delay)
        return target()


class CorruptPort(_Wrapper):
    """Returns ``payload`` instead of the real answer on each faulty call."""

    def __init__(
        self,
        inner: Any,
        payload: Any,
        *,
        methods: Collection[str] | None = None,
    ) -> None:
        super().__init__(inner, methods)
        self._payload = payload

    def _intercept(self, name: str, target: Callable[..., Any]) -> Any:
        return self._payload


class ScriptedModel:
    """Stands in for the bound chat model: raises or returns the queued items in order."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, messages: Any) -> Any:
        self.calls += 1
        outcome = (
            self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Reply:
    def __init__(self, tool_calls: list[dict[str, Any]]) -> None:
        self.tool_calls = tool_calls
