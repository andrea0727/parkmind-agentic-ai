"""Hard/soft classification accuracy of the ``elicit`` extraction.

Each extracted statement is keyed (``ElicitationExtraction.classified_items``)
and labelled ``hard`` or ``soft``. A labeled fixture holds the expected key ->
label map; this compares the two. ``hard_as_soft`` is the dangerous error -- a
constraint (above all an accessibility statement) that would be traded off --
so it is reported on its own.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassificationReport:
    total: int
    correct: int
    hard_as_soft: int
    soft_as_hard: int
    missing: int
    spurious: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0


def classification_report(
    predicted: Mapping[str, str], expected: Mapping[str, str]
) -> ClassificationReport:
    """Compare one extraction with its labels.

    ``total`` is the number of expected items. ``missing`` expected items were
    not extracted; ``spurious`` extracted items were not expected (they do not
    count towards ``total`` but are reported).
    """
    correct = hard_as_soft = soft_as_hard = missing = 0
    for key, label in expected.items():
        got = predicted.get(key)
        if got is None:
            missing += 1
        elif got == label:
            correct += 1
        elif label == "hard":
            hard_as_soft += 1
        else:
            soft_as_hard += 1
    return ClassificationReport(
        total=len(expected),
        correct=correct,
        hard_as_soft=hard_as_soft,
        soft_as_hard=soft_as_hard,
        missing=missing,
        spurious=len(set(predicted) - set(expected)),
    )


def combine_reports(reports: Iterable[ClassificationReport]) -> ClassificationReport:
    items = list(reports)
    return ClassificationReport(
        total=sum(r.total for r in items),
        correct=sum(r.correct for r in items),
        hard_as_soft=sum(r.hard_as_soft for r in items),
        soft_as_hard=sum(r.soft_as_hard for r in items),
        missing=sum(r.missing for r in items),
        spurious=sum(r.spurious for r in items),
    )
