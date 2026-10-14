"""Run one extraction repeatedly and report every hard constraint that is not identical across runs."""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

pytest.importorskip("deepeval")

from evals.eval_metrics import (
    ConstraintSnapshot,
    snapshot_from_extraction,
)
from evals.eval_thresholds import DEFAULT_RUNS
from parkmind.agents.elicit_agent import (
    GuestInfoExtractor,
    extract_with_recovery,
)
from parkmind.agents.elicit_schema import normalize_name

RUNS_ENV = "PARKMIND_EVAL_RUNS"
FIELDS = ("party_size", "heights_cm", "departure_time", "must_do", "avoid")
NO_EXTRACTION = "no valid extraction"


def eval_runs() -> int:
    raw = os.getenv(RUNS_ENV)
    if raw is None:
        return DEFAULT_RUNS
    try:
        runs = int(raw)
    except ValueError:
        raise ValueError(f"{RUNS_ENV} must be a whole number, got {raw!r}") from None
    if runs < 1:
        raise ValueError(f"{RUNS_ENV} must be at least 1, got {runs}")
    return runs


@dataclass(frozen=True)
class ConsistencyReport:
    snapshots: Sequence[ConstraintSnapshot | None]
    divergent_fields: tuple[str, ...] = field(default=())

    @property
    def consistent(self) -> bool:
        return not self.divergent_fields


def _comparable(snapshot: ConstraintSnapshot, name: str) -> object:
    value = getattr(snapshot, name)
    if name in ("must_do", "avoid"):
        return sorted(normalize_name(item) for item in value)
    return value


def compare(snapshots: Sequence[ConstraintSnapshot | None]) -> ConsistencyReport:
    if any(snapshot is None for snapshot in snapshots):
        return ConsistencyReport(snapshots, (NO_EXTRACTION,))
    present = [snapshot for snapshot in snapshots if snapshot is not None]
    divergent = tuple(
        name
        for name in FIELDS
        if any(_comparable(s, name) != _comparable(present[0], name) for s in present)
    )
    return ConsistencyReport(snapshots, divergent)


def check_consistency(
    extractor: GuestInfoExtractor, messages: Sequence[str], runs: int | None = None
) -> ConsistencyReport:
    snapshots: list[ConstraintSnapshot | None] = []
    for _ in range(runs or eval_runs()):
        outcome = extract_with_recovery(extractor, messages)
        snapshots.append(
            snapshot_from_extraction(outcome.extraction) if outcome.extraction else None
        )
    return compare(snapshots)
