"""The full guest journey stays fast: message, confirmation, approval."""

import statistics

from performance.perf_budgets import (
    JOURNEY_P50_SECONDS,
    JOURNEY_P95_SECONDS,
    JOURNEY_RUNS,
)
from performance.perf_harness import (
    build_journey,
    journey_message,
    percentile,
    timed,
)


def _durations(runs: int) -> list[float]:
    journey = build_journey()
    journey.run("warm-up", journey_message("Warm", 2, "TRON"))
    durations = []
    for n in range(runs):
        _, seconds = timed(
            lambda n=n: journey.run(f"run-{n}", journey_message(f"R{n}", 2, "TRON"))
        )
        assert journey.active_plan(f"run-{n}") is not None
        durations.append(seconds)
    return durations


def test_the_median_journey_is_within_budget() -> None:
    durations = _durations(JOURNEY_RUNS)

    p50 = statistics.median(durations)

    assert p50 < JOURNEY_P50_SECONDS, (
        f"p50 {p50:.3f}s over {len(durations)} runs (budget {JOURNEY_P50_SECONDS:.2f}s)"
    )


def test_the_slow_tail_of_journeys_is_within_budget() -> None:
    durations = _durations(JOURNEY_RUNS)

    p95 = percentile(durations, 0.95)

    assert p95 < JOURNEY_P95_SECONDS, (
        f"p95 {p95:.3f}s, max {max(durations):.3f}s over {len(durations)} runs "
        f"(budget {JOURNEY_P95_SECONDS:.2f}s)"
    )


def test_a_later_journey_is_not_slower_than_an_early_one_on_the_same_graph() -> None:
    durations = _durations(max(JOURNEY_RUNS, 10))
    half = len(durations) // 2

    early, late = (
        statistics.median(durations[:half]),
        statistics.median(durations[half:]),
    )

    assert late < max(early * 5, 0.25), (
        f"early median {early:.3f}s, late median {late:.3f}s: a run's cost grows with history"
    )
