"""Pass rate over repeated runs: the honest way to score something a model words differently each time."""

import os
from collections.abc import Sequence

from evals.eval_thresholds import CONVERSATION_RUNS, PASS_RATE


def runs() -> int:
    return int(os.getenv("PARKMIND_CONVERSATION_RUNS", str(CONVERSATION_RUNS)))


def pass_rate(outcomes: Sequence[bool]) -> float:
    return sum(outcomes) / len(outcomes) if outcomes else 0.0


def meets(outcomes: Sequence[bool], required: float = PASS_RATE) -> bool:
    return pass_rate(outcomes) >= required
