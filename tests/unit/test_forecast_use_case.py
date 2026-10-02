"""Wiring the ForecastService from stored snapshots (P0-18)."""

from datetime import datetime, timedelta

import pytest
from factories import live_context
from fakes import InMemorySnapshotRepository

from parkmind.core.contracts import PARK_TZ, AttractionStatus, DataSource, WaitEstimate
from parkmind.services.use_cases.forecast import build_wait_profile

DAY = datetime(2026, 9, 20, tzinfo=PARK_TZ)


def _store(
    snapshots: InMemorySnapshotRepository,
    at: datetime,
    waits: dict[str, float],
    *,
    status: AttractionStatus = AttractionStatus.OPERATING,
) -> None:
    context = live_context(
        snapshot_id=f"snap-{at.isoformat()}",
        retrieved_at=at,
        waits={a: WaitEstimate(attraction_id=a, wait_minutes=w, status=status) for a, w in waits.items()},
    )
    snapshots.save(context, {}, [DataSource.THEMEPARKS_WIKI], normalizer_version=2)


def _days(hour: int, minute: int, n: int) -> list[datetime]:
    return [DAY.replace(hour=hour, minute=minute) + timedelta(days=d) for d in range(n)]


# --- build_wait_profile ------------------------------------------------------------


def test_profile_is_the_median_per_attraction_and_park_hour() -> None:
    snapshots = InMemorySnapshotRepository()
    for at, wait in zip(_days(14, 10, 3), [30.0, 60.0, 40.0], strict=True):
        _store(snapshots, at, {"a1": wait})
    _store(snapshots, DAY.replace(hour=14, minute=50) + timedelta(days=3), {"a1": 50.0})
    now = DAY + timedelta(days=5)

    profile = build_wait_profile(snapshots, now=now)

    assert profile.medians == {("a1", 14): 45.0}
    assert profile.sample_counts == {("a1", 14): 4}
    assert profile.built_at == now


def test_a_cell_with_too_few_samples_has_no_profile() -> None:
    snapshots = InMemorySnapshotRepository()
    for at in _days(10, 0, 2):
        _store(snapshots, at, {"a1": 20.0})

    assert build_wait_profile(snapshots, now=DAY + timedelta(days=5)).medians == {}
    assert build_wait_profile(snapshots, now=DAY + timedelta(days=5), min_samples=2).medians == {
        ("a1", 10): 20.0
    }


def test_snapshots_after_now_are_not_in_the_profile() -> None:
    snapshots = InMemorySnapshotRepository()
    for at in _days(10, 0, 3):
        _store(snapshots, at, {"a1": 20.0})
    for at in _days(10, 0, 6)[3:]:
        _store(snapshots, at, {"a1": 90.0})

    profile = build_wait_profile(snapshots, now=DAY + timedelta(days=2, hours=12))

    assert profile.medians == {("a1", 10): 20.0}


def test_waits_of_rides_that_are_not_operating_are_not_in_the_profile() -> None:
    snapshots = InMemorySnapshotRepository()
    for at in _days(10, 0, 3):
        _store(snapshots, at, {"a1": 20.0}, status=AttractionStatus.DOWN)

    assert build_wait_profile(snapshots, now=DAY + timedelta(days=5)).medians == {}


def test_profile_is_deterministic() -> None:
    snapshots = InMemorySnapshotRepository()
    for i, at in enumerate(_days(9, 30, 4) + _days(15, 5, 4)):
        _store(snapshots, at, {"a1": 10.0 + i, "a2": 30.0 - i})
    now = DAY + timedelta(days=6)

    assert build_wait_profile(snapshots, now=now) == build_wait_profile(snapshots, now=now)


@pytest.mark.parametrize("kwargs", [{"now": DAY.replace(tzinfo=None)}, {"now": DAY, "min_samples": 0}])
def test_profile_rejects_naive_now_and_empty_threshold(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        build_wait_profile(InMemorySnapshotRepository(), **kwargs)
