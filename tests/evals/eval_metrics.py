"""Deterministic metrics: no judge, so the same output always gets the same score."""

import re
from collections.abc import Collection, Mapping
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

pytest.importorskip("deepeval")

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

from evals.eval_thresholds import ACCURACY, GROUNDEDNESS
from parkmind.agents.elicit_schema import (
    ElicitationExtraction,
    normalize_name,
)
from parkmind.agents.grounding import ungrounded_numerals
from parkmind.core.contracts import PARK_TZ, PartyConstraints
from parkmind.core.language import DEFAULT_LANGUAGE, Language


class ConstraintSnapshot(BaseModel):
    """The hard constraints of one party, in the shape both sides of a comparison share."""

    model_config = ConfigDict(extra="forbid")

    party_size: int | None
    heights_cm: list[float | None]
    departure_time: str | None
    must_do: list[str]
    avoid: list[str]


def snapshot_from_extraction(extraction: ElicitationExtraction) -> ConstraintSnapshot:
    return ConstraintSnapshot(
        party_size=extraction.party_size,
        heights_cm=[guest.height_cm for guest in extraction.guests],
        departure_time=extraction.departure_time,
        must_do=list(extraction.must_do),
        avoid=list(extraction.avoid),
    )


def snapshot_from_constraints(
    constraints: PartyConstraints, names: Mapping[str, str]
) -> ConstraintSnapshot:
    return ConstraintSnapshot(
        party_size=constraints.party_size,
        heights_cm=[guest.height_cm for guest in constraints.guests],
        departure_time=f"{constraints.departure_time.astimezone(PARK_TZ):%H:%M}",
        must_do=[names[node_id] for node_id in constraints.must_do],
        avoid=[names[node_id] for node_id in constraints.avoid],
    )


def constraint_test_case(
    actual: ConstraintSnapshot, expected: ConstraintSnapshot, *, request: str = ""
) -> LLMTestCase:
    return LLMTestCase(
        input=request,
        actual_output=actual.model_dump_json(),
        expected_output=expected.model_dump_json(),
    )


def _names(values: Collection[str]) -> set[str]:
    return {normalize_name(value) for value in values}


class ConstraintPreservationMetric(BaseMetric):
    """Extracted hard constraints equal the labeled ones: size, heights, departure, must-do, avoid."""

    def __init__(self, threshold: float = ACCURACY) -> None:
        self.threshold = threshold
        self.async_mode = False
        self.strict_mode = False
        self.include_reason = True
        self.evaluation_model = None
        self.error: str | None = None

    @property
    def __name__(self) -> str:
        return "Constraint Preservation"

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        actual = ConstraintSnapshot.model_validate_json(test_case.actual_output or "")
        expected = ConstraintSnapshot.model_validate_json(
            test_case.expected_output or ""
        )
        checks: dict[str, str | None] = {
            "party_size": self._equal(
                "party size", actual.party_size, expected.party_size
            ),
            "heights": self._equal("heights", actual.heights_cm, expected.heights_cm),
            "departure": self._equal(
                "departure", actual.departure_time, expected.departure_time
            ),
            "must_do": self._same_names("must-do", actual.must_do, expected.must_do),
            "avoid": self._same_names("avoid", actual.avoid, expected.avoid),
        }
        failures = [message for message in checks.values() if message]
        self.score = (len(checks) - len(failures)) / len(checks)
        self.reason = "; ".join(failures) or "every hard constraint matches"
        self.success = self.is_successful()
        return self.score

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)

    @staticmethod
    def _equal(label: str, actual: object, expected: object) -> str | None:
        return None if actual == expected else f"{label} {actual} should be {expected}"

    @staticmethod
    def _same_names(
        label: str, actual: Collection[str], expected: Collection[str]
    ) -> str | None:
        dropped = sorted(_names(expected) - _names(actual))
        added = sorted(_names(actual) - _names(expected))
        if not dropped and not added:
            return None
        parts = [
            f"dropped {dropped}" if dropped else "",
            f"invented {added}" if added else "",
        ]
        return f"{label}: {' and '.join(part for part in parts if part)}"


class GroundednessMetric(BaseMetric):
    """Every number, time and attraction name in an explanation exists in the plan payload."""

    def __init__(
        self,
        known_attractions: Collection[str],
        *,
        language: Language = DEFAULT_LANGUAGE,
        threshold: float = GROUNDEDNESS,
    ) -> None:
        self.threshold = threshold
        self.known_attractions = sorted(known_attractions)
        self.language: Language = language
        self.async_mode = False
        self.strict_mode = False
        self.include_reason = True
        self.evaluation_model = None
        self.error: str | None = None

    @property
    def __name__(self) -> str:
        return "Groundedness"

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        explanation = test_case.actual_output or ""
        payload = "\n".join(str(item) for item in test_case.retrieval_context or [])
        invented_numerals = ungrounded_numerals(explanation, payload, self.language)
        invented_names = [
            name
            for name in self.known_attractions
            if _mentions(explanation, name) and not _mentions(payload, name)
        ]
        findings = [
            f"figures not in the plan: {invented_numerals}"
            if invented_numerals
            else "",
            f"attractions not in the plan: {invented_names}" if invented_names else "",
        ]
        problems = [finding for finding in findings if finding]
        self.score = 0.0 if problems else 1.0
        self.reason = (
            "; ".join(problems) or "every figure and attraction is in the plan"
        )
        self.success = self.is_successful()
        return self.score

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)


def _mentions(text: str, name: str) -> bool:
    return (
        re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None
    )


def explanation_test_case(explanation: str, payload_json: str) -> LLMTestCase:
    return LLMTestCase(
        input="Explain this plan to the guests.",
        actual_output=explanation,
        retrieval_context=[payload_json],
    )
