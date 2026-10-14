"""Narrators for graph tests: no test may call Claude, even with a key in .env."""

from collections.abc import Mapping
from typing import Any

from parkmind.agents.explain_agent import NarratorUnavailableError


class NoNarrator:
    """Like a missing API key: EXPLAIN uses the template."""

    def narrate(self, payload_json: str, repair_hint: str | None = None) -> Mapping[str, Any]:
        raise NarratorUnavailableError("tests never call the LLM")


class ScriptedNarrator:
    """Answers with ``texts`` in order and records the payloads it was given."""

    def __init__(self, *texts: str) -> None:
        self._texts = list(texts)
        self.payloads: list[str] = []

    def narrate(self, payload_json: str, repair_hint: str | None = None) -> Mapping[str, Any]:
        self.payloads.append(payload_json)
        return {"text": self._texts.pop(0)}
