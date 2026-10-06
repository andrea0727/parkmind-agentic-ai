"""Shared fakes and fixture loading for the elicit tests."""

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from parkmind.core.contracts import AccessibilityRequirements
from parkmind.services.use_cases.accessibility_intake import AccessibilityIntakeUseCase

SCENARIOS_PATH = Path(__file__).parent / "fixtures" / "elicit" / "scenarios.json"


def load_scenarios() -> list[dict[str, Any]]:
    return json.loads(SCENARIOS_PATH.read_text())["scenarios"]


def scenario(scenario_id: str) -> dict[str, Any]:
    return next(s for s in load_scenarios() if s["id"] == scenario_id)


class FakeExtractor:
    """Returns queued outputs in order (the last one repeats); raises queued exceptions."""

    def __init__(self, *outputs: Mapping[str, Any] | Exception) -> None:
        self._outputs = list(outputs)
        self.calls: list[tuple[list[str], str | None]] = []

    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        self.calls.append((list(human_messages), repair_hint))
        output = self._outputs.pop(0) if len(self._outputs) > 1 else self._outputs[0]
        if isinstance(output, Exception):
            raise output
        return output


class FakeSessionStore:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], AccessibilityRequirements] = {}
        self.puts = 0

    def put(self, session_id: str, requirements: AccessibilityRequirements) -> None:
        self.puts += 1
        self.records[(session_id, requirements.guest_id)] = requirements

    def get(self, session_id: str, guest_id: str) -> AccessibilityRequirements | None:
        return self.records.get((session_id, guest_id))

    def end_session(self, session_id: str) -> None:
        self.records = {k: v for k, v in self.records.items() if k[0] != session_id}


def make_intake() -> tuple[AccessibilityIntakeUseCase, FakeSessionStore]:
    store = FakeSessionStore()

    @contextmanager
    def factory() -> Iterator[FakeSessionStore]:
        yield store

    return AccessibilityIntakeUseCase(store_factory=factory), store
