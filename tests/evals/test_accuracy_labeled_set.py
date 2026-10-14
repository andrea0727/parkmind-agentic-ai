"""Replaying the labeled scenarios through the real validation and build path keeps every hard constraint."""

import os
from datetime import date
from typing import Any

import pytest

pytest.importorskip("deepeval")

from elicit_support import (
    FakeExtractor,
    catalog,
    load_scenarios,
    make_names,
    scenario,
)

from evals import eval_scenarios
from evals.eval_metrics import (
    ConstraintPreservationMetric,
    ConstraintSnapshot,
    constraint_test_case,
    snapshot_from_constraints,
    snapshot_from_extraction,
)
from evals.eval_thresholds import ACCURACY
from parkmind.agents.elicit_agent import (
    AnthropicExtractor,
    build_party_constraints,
    extract_with_recovery,
    missing_information,
)
from parkmind.agents.elicit_schema import ElicitationExtraction

EXPECTATIONS = eval_scenarios.load_expectations()
SCENARIO_IDS = [s["id"] for s in load_scenarios()]
NODE_NAMES = {a.node_id: a.name for a in catalog()}
TODAY = date(2026, 9, 16)


def extracted(raw: Any, messages: list[str]) -> ElicitationExtraction:
    outcome = extract_with_recovery(FakeExtractor(raw), messages, max_attempts=1)
    assert outcome.extraction is not None, outcome.errors
    return outcome.extraction


def score(actual: ConstraintSnapshot, scenario_id: str) -> ConstraintPreservationMetric:
    metric = ConstraintPreservationMetric()
    metric.measure(constraint_test_case(actual, EXPECTATIONS[scenario_id]))
    return metric


def built(extraction: ElicitationExtraction) -> ConstraintSnapshot:
    resolution = make_names().resolve([*extraction.must_do, *extraction.avoid])
    assert resolution.complete
    constraints = build_party_constraints(
        extraction, today=TODAY, resolution=resolution
    )
    return snapshot_from_constraints(constraints, NODE_NAMES)


def test_every_scenario_has_a_labeled_expectation_and_no_label_is_orphaned() -> None:
    assert set(EXPECTATIONS) == set(SCENARIO_IDS)


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_the_validated_extraction_preserves_every_labeled_constraint(
    scenario_id: str,
) -> None:
    case = scenario(scenario_id)
    extraction = extracted(case["extraction"], case["messages"])
    metric = score(snapshot_from_extraction(extraction), scenario_id)
    assert metric.is_successful(), metric.reason


@pytest.mark.parametrize(
    "scenario_id",
    [s["id"] for s in load_scenarios() if not s["expect_missing"]],
)
def test_the_built_party_constraints_preserve_every_labeled_constraint(
    scenario_id: str,
) -> None:
    case = scenario(scenario_id)
    extraction = extracted(case["extraction"], case["messages"])
    assert missing_information(extraction) == []
    metric = score(built(extraction), scenario_id)
    assert metric.is_successful(), metric.reason


@pytest.mark.parametrize(
    "scenario_id",
    [s["id"] for s in load_scenarios() if s["expect_missing"]],
)
def test_a_scenario_with_missing_information_is_not_built_into_constraints(
    scenario_id: str,
) -> None:
    case = scenario(scenario_id)
    extraction = extracted(case["extraction"], case["messages"])
    assert missing_information(extraction) == case["expect_missing"]


def test_accuracy_over_the_whole_labeled_set_meets_the_threshold() -> None:
    scores = []
    for case in load_scenarios():
        extraction = extracted(case["extraction"], case["messages"])
        scores.append(
            score(snapshot_from_extraction(extraction), case["id"]).score or 0.0
        )
    assert sum(scores) / len(scores) >= ACCURACY


def test_the_labeled_set_covers_stated_heights_and_avoided_attractions() -> None:
    assert any(any(h for h in e.heights_cm) for e in EXPECTATIONS.values())
    assert any(e.avoid for e in EXPECTATIONS.values())
    assert any(e.must_do for e in EXPECTATIONS.values())


def test_the_harness_notices_a_must_do_dropped_from_a_labeled_scenario() -> None:
    case = scenario("wiki_example_complete")
    degraded = {**case["extraction"], "must_do": ["TRON"]}
    metric = score(
        snapshot_from_extraction(extracted(degraded, case["messages"])),
        "wiki_example_complete",
    )
    assert not metric.is_successful()


def test_the_harness_notices_a_height_changed_after_extraction() -> None:
    case = scenario("stated_child_height")
    extraction = extracted(case["extraction"], case["messages"])
    altered = snapshot_from_extraction(extraction).model_copy(
        update={"heights_cm": [None, 200]}
    )
    assert not score(altered, "stated_child_height").is_successful()


live = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY"
)


@pytest.mark.live
@live
@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_the_real_model_preserves_every_labeled_constraint(scenario_id: str) -> None:
    outcome = extract_with_recovery(
        AnthropicExtractor(), scenario(scenario_id)["messages"]
    )
    assert outcome.extraction is not None, outcome.errors
    metric = score(snapshot_from_extraction(outcome.extraction), scenario_id)
    assert metric.is_successful(), metric.reason


@pytest.mark.live
@live
def test_the_real_model_extracts_language_independent_values_from_spanish() -> None:
    messages = [
        "Somos dos adultos y queremos ir a Montaña Espacial. Salimos a las 6 de la tarde."
    ]
    outcome = extract_with_recovery(AnthropicExtractor(), messages)
    assert outcome.extraction is not None, outcome.errors
    assert outcome.extraction.departure_time == "18:00"
    assert [name.lower() for name in outcome.extraction.must_do] == ["space mountain"]
