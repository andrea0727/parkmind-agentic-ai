"""Repeating the same extraction must leave every hard constraint unchanged; a flip is reported."""

import os
from typing import Any

import pytest

pytest.importorskip("deepeval")

from elicit_support import FakeExtractor, scenario

from evals.eval_consistency import (
    NO_EXTRACTION,
    RUNS_ENV,
    check_consistency,
    eval_runs,
)
from evals.eval_thresholds import DEFAULT_RUNS
from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.agents.elicit_schema import ElicitationExtraction

MESSAGES = scenario("stated_child_height")["messages"]
STABLE: dict[str, Any] = scenario("stated_child_height")["extraction"]


def flipped(**changes: Any) -> dict[str, Any]:
    return {**STABLE, **changes}


def test_the_run_count_defaults_to_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RUNS_ENV, raising=False)
    assert eval_runs() == DEFAULT_RUNS == 3


def test_the_run_count_can_be_raised_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RUNS_ENV, "5")
    assert eval_runs() == 5


@pytest.mark.parametrize("raw", ["many", "0", "-2", ""])
def test_a_run_count_that_is_not_a_positive_number_is_rejected(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(RUNS_ENV, raw)
    with pytest.raises(ValueError, match=RUNS_ENV):
        eval_runs()


def test_an_extractor_that_repeats_itself_is_consistent() -> None:
    extractor = FakeExtractor(STABLE)
    report = check_consistency(extractor, MESSAGES, runs=3)
    assert report.consistent
    assert len(extractor.calls) == 3


def test_the_default_number_of_runs_is_used_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUNS_ENV, raising=False)
    extractor = FakeExtractor(STABLE)
    check_consistency(extractor, MESSAGES)
    assert len(extractor.calls) == DEFAULT_RUNS


def test_a_height_that_flips_on_one_run_is_reported() -> None:
    child_200 = flipped(guests=[{"role": "adult"}, {"role": "child", "height_cm": 200}])
    report = check_consistency(
        FakeExtractor(STABLE, child_200, STABLE), MESSAGES, runs=3
    )
    assert not report.consistent
    assert report.divergent_fields == ("heights_cm",)


def test_a_departure_time_that_flips_is_reported() -> None:
    report = check_consistency(
        FakeExtractor(STABLE, flipped(departure_time="19:00")), MESSAGES, runs=3
    )
    assert report.divergent_fields == ("departure_time",)


def test_a_must_do_that_appears_on_a_later_run_is_reported() -> None:
    report = check_consistency(
        FakeExtractor(STABLE, flipped(must_do=["TRON"])), MESSAGES, runs=2
    )
    assert report.divergent_fields == ("must_do",)


def test_a_party_size_that_flips_is_reported() -> None:
    three = flipped(party_size=3, guests=[*STABLE["guests"], {"role": "adult"}])
    report = check_consistency(FakeExtractor(STABLE, three), MESSAGES, runs=2)
    assert "party_size" in report.divergent_fields


def test_must_do_order_and_case_are_not_divergence() -> None:
    first = flipped(must_do=["TRON", "Space Mountain"])
    second = flipped(must_do=["space mountain", "tron"])
    assert check_consistency(FakeExtractor(first, second), MESSAGES, runs=2).consistent


def test_soft_preferences_may_vary_without_breaking_consistency() -> None:
    varied = flipped(guests=[{"role": "adult", "pace": "relaxed"}, STABLE["guests"][1]])
    assert check_consistency(FakeExtractor(STABLE, varied), MESSAGES, runs=2).consistent


def test_a_run_that_yields_no_valid_extraction_is_reported() -> None:
    invalid = flipped(departure_time="six pm")
    report = check_consistency(FakeExtractor(STABLE, invalid), MESSAGES, runs=2)
    assert report.divergent_fields == (NO_EXTRACTION,)
    assert report.snapshots[1] is None


def test_the_stable_fixture_is_itself_a_valid_extraction() -> None:
    ElicitationExtraction.model_validate(STABLE)


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
@pytest.mark.parametrize("scenario_id", ["stated_child_height", "wheelchair_and_avoid"])
def test_the_real_model_keeps_hard_constraints_identical_across_runs(
    scenario_id: str,
) -> None:
    report = check_consistency(AnthropicExtractor(), scenario(scenario_id)["messages"])
    assert report.consistent, report.divergent_fields
