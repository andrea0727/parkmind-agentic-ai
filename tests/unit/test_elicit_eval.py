"""Live hard/soft classification accuracy of the real model.

Skipped without ANTHROPIC_API_KEY. Runs every labeled scenario through
``AnthropicExtractor`` and measures accuracy against the hand labels.
"""

import os

import pytest
from elicit_support import load_scenarios

from parkmind.agents.elicit_agent import AnthropicExtractor, extract_with_recovery
from parkmind.services.evaluation.metrics.extraction import (
    classification_report,
    combine_reports,
)

MIN_ACCURACY = 0.85


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_live_classification_accuracy_over_the_labeled_set() -> None:
    extractor = AnthropicExtractor()
    reports = []
    for scenario in load_scenarios():
        outcome = extract_with_recovery(extractor, scenario["messages"])
        assert outcome.extraction is not None, scenario["id"]
        reports.append(
            classification_report(outcome.extraction.classified_items(), scenario["expected"])
        )

    total = combine_reports(reports)
    print(f"elicit classification: {total}")

    assert total.hard_as_soft == 0, total  # a constraint treated as tradeable is never acceptable
    assert total.accuracy >= MIN_ACCURACY, total
