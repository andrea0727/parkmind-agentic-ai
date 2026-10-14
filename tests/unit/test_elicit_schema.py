"""Structured extraction schema and the labeled fixture set."""

from typing import Any

import pytest
from elicit_support import load_scenarios
from pydantic import ValidationError

from parkmind.agents.elicit_schema import ElicitationExtraction
from parkmind.services.evaluation.metrics.extraction import (
    classification_report,
    combine_reports,
)

SCENARIOS = load_scenarios()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_recorded_output_is_valid_and_classified_as_labeled(
    scenario: dict[str, Any],
) -> None:
    extraction = ElicitationExtraction.model_validate(scenario["extraction"])

    report = classification_report(extraction.classified_items(), scenario["expected"])

    assert report.accuracy == 1.0, report
    assert report.spurious == 0


def test_fixture_set_covers_accessibility_and_preferences() -> None:
    reports = []
    labels: set[str] = set()
    for s in SCENARIOS:
        labels.update(s["expected"].values())
        extraction = ElicitationExtraction.model_validate(s["extraction"])
        reports.append(
            classification_report(extraction.classified_items(), s["expected"])
        )

    total = combine_reports(reports)
    assert labels == {"hard", "soft"}
    assert total.hard_as_soft == 0
    assert total.accuracy == 1.0
    assert any(k.startswith("accessibility:") for s in SCENARIOS for k in s["expected"])


def test_accessibility_is_never_classified_soft() -> None:
    extraction = ElicitationExtraction.model_validate(
        {
            "guests": [{"role": "adult"}],
            "accessibility": [
                {"guest_ref": 1, "mobility_requirements": ["WHEELCHAIR"]}
            ],
        }
    )

    items = extraction.classified_items()

    assert items == {"accessibility:g1:WHEELCHAIR": "hard"}


@pytest.mark.parametrize(
    "payload",
    [
        {"party_size": 3, "guests": [{"role": "adult"}]},  # size/guests mismatch
        {
            "guests": [{"role": "adult"}],
            "accessibility": [{"guest_ref": 2, "heat_sensitivity": True}],
        },
        {"departure_time": "8 PM"},
        {"departure_time": "25:00"},
        {"lunch_window": {"start": "13:30", "end": "12:30"}},
        {"guests": [{"role": "adult"}], "accessibility": [{"guest_ref": 1}]},  # no flag
        {"guests": [{"role": "teenager"}]},
        {"guests": [{"queue_tolerance": 1.5}]},
        {"unexpected": 1},
    ],
)
def test_invalid_output_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ElicitationExtraction.model_validate(payload)


@pytest.mark.parametrize("field", ["consent", "retention_policy", "notes", "condition"])
def test_schema_has_no_consent_or_free_text_health_field(field: str) -> None:
    """Consent and retention come from the human; health text has no slot [C15, C19]."""
    with pytest.raises(ValidationError):
        ElicitationExtraction.model_validate(
            {
                "guests": [{"role": "adult"}],
                "accessibility": [
                    {
                        "guest_ref": 1,
                        "mobility_requirements": ["WHEELCHAIR"],
                        field: "x",
                    }
                ],
            }
        )


@pytest.mark.parametrize("height", [20, 49.9, 250.1, 400])
def test_a_height_outside_the_human_range_is_rejected(height: float) -> None:
    """Scenario 29 ("400 cm and 20 cm"): the schema refuses it; the agent must ask, not clamp."""
    with pytest.raises(ValidationError):
        ElicitationExtraction.model_validate({"guests": [{"role": "adult", "height_cm": height}]})


@pytest.mark.parametrize("height", [50, 250])
def test_the_height_range_bounds_are_accepted(height: float) -> None:
    extraction = ElicitationExtraction.model_validate(
        {"guests": [{"role": "adult", "height_cm": height}]}
    )

    assert extraction.guests[0].height_cm == height
