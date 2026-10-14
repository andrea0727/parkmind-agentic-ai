"""Many sessions on one graph and one checkpointer stay correct and independent."""

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from performance.perf_budgets import CONCURRENT_THREADS, CONCURRENT_TOTAL_SECONDS
from performance.perf_harness import (
    RIDES,
    Journey,
    build_journey,
    interrupt_payload,
    journey_message,
    label_for,
    timed,
)

BARRIER_TIMEOUT_SECONDS = 30
RIDE_NAMES = list(RIDES)


@pytest.fixture(scope="module")
def run() -> dict[str, Any]:
    """The concurrent journeys, run once; every test reads their outcomes."""
    journey = build_journey()
    count = CONCURRENT_THREADS
    barrier = threading.Barrier(count, timeout=BARRIER_TIMEOUT_SECONDS)
    expected = {
        f"thr-{n:02d}": {
            "label": label_for(n),
            "party": 2 + n % 4,
            "ride": RIDE_NAMES[n % len(RIDE_NAMES)],
        }
        for n in range(count)
    }

    def session(thread_id: str) -> dict[str, Any]:
        spec = expected[thread_id]
        message = journey_message(spec["label"], spec["party"], spec["ride"])
        first = journey.start(thread_id, message)
        barrier.wait()
        second = journey.confirm(thread_id)
        barrier.wait()
        third = journey.approve(thread_id)
        return {"first": first, "second": second, "third": third}

    def everything() -> dict[str, Any]:
        with ThreadPoolExecutor(max_workers=count) as pool:
            futures = {tid: pool.submit(session, tid) for tid in expected}
            return {tid: f.result(timeout=120) for tid, f in futures.items()}

    results, seconds = timed(everything)
    return {
        "journey": journey,
        "expected": expected,
        "results": results,
        "seconds": seconds,
    }


def test_every_session_finishes_without_an_exception(run: dict[str, Any]) -> None:
    assert set(run["results"]) == set(run["expected"])
    for outcome in run["results"].values():
        assert "__interrupt__" not in outcome["third"]


def test_every_session_ends_with_its_own_active_plan(run: dict[str, Any]) -> None:
    journey: Journey = run["journey"]
    for thread_id, spec in run["expected"].items():
        plan = journey.active_plan(thread_id)
        assert plan is not None, thread_id
        assert RIDES[spec["ride"]] in [stop.node_id for stop in plan.stops], thread_id


def test_every_session_keeps_its_own_party_and_must_do(run: dict[str, Any]) -> None:
    journey: Journey = run["journey"]
    for thread_id, spec in run["expected"].items():
        constraints = journey.values(thread_id)["constraints"]
        assert constraints.party_size == spec["party"], thread_id
        assert constraints.must_do == [RIDES[spec["ride"]]], thread_id
        assert constraints.guests[0].label == spec["label"], thread_id


def test_no_session_sees_another_sessions_label(run: dict[str, Any]) -> None:
    journey: Journey = run["journey"]
    labels = {tid: spec["label"] for tid, spec in run["expected"].items()}
    for thread_id, label in labels.items():
        visible = repr([interrupt_payload(r) for r in journey.seen[thread_id]])
        visible += repr({k: v for k, v in journey.values(thread_id).items()})
        foreign = [
            other
            for tid, other in labels.items()
            if tid != thread_id and other in visible
        ]
        assert foreign == [], f"{thread_id} shows {foreign}"
        assert label in visible


def test_each_session_has_exactly_one_proposal_and_it_is_its_own(
    run: dict[str, Any],
) -> None:
    journey: Journey = run["journey"]
    owners = [thread_id for thread_id, _ in journey.proposals()]

    assert sorted(owners) == sorted(run["expected"])


def test_the_extractor_and_narrator_are_called_once_per_session(
    run: dict[str, Any],
) -> None:
    journey: Journey = run["journey"]

    assert journey.extractor.calls == CONCURRENT_THREADS
    assert journey.narrator.calls == CONCURRENT_THREADS


def test_all_sessions_finish_within_the_time_budget(run: dict[str, Any]) -> None:
    assert run["seconds"] < CONCURRENT_TOTAL_SECONDS, (
        f"{CONCURRENT_THREADS} sessions took {run['seconds']:.2f}s "
        f"(budget {CONCURRENT_TOTAL_SECONDS:.1f}s)"
    )
