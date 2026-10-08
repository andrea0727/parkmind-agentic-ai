"""Hard/soft classification accuracy metric."""

from parkmind.services.evaluation.metrics.extraction import (
    classification_report,
    combine_reports,
)


def test_perfect_classification() -> None:
    labels = {"must_do:x": "hard", "pace:g1": "soft"}

    report = classification_report(labels, labels)

    assert (report.total, report.correct, report.accuracy) == (2, 2, 1.0)
    assert report.hard_as_soft == report.soft_as_hard == report.missing == report.spurious == 0


def test_hard_as_soft_is_reported_separately_from_soft_as_hard() -> None:
    expected = {"accessibility:g2:WHEELCHAIR": "hard", "pace:g1": "soft", "must_do:x": "hard"}
    predicted = {
        "accessibility:g2:WHEELCHAIR": "soft",  # the dangerous mistake
        "pace:g1": "hard",
        "must_do:x": "hard",
    }

    report = classification_report(predicted, expected)

    assert report.hard_as_soft == 1
    assert report.soft_as_hard == 1
    assert report.correct == 1
    assert report.accuracy == 1 / 3


def test_missing_and_spurious_items() -> None:
    report = classification_report({"a": "hard", "extra": "soft"}, {"a": "hard", "b": "soft"})

    assert (report.total, report.correct, report.missing, report.spurious) == (2, 1, 1, 1)
    assert report.accuracy == 0.5


def test_empty_expectation_is_vacuously_accurate() -> None:
    assert classification_report({}, {}).accuracy == 1.0


def test_combine_reports_sums_counts() -> None:
    a = classification_report({"x": "hard"}, {"x": "hard"})
    b = classification_report({"y": "soft"}, {"y": "hard"})

    total = combine_reports([a, b])

    assert (total.total, total.correct, total.hard_as_soft) == (2, 1, 1)
    assert total.accuracy == 0.5
