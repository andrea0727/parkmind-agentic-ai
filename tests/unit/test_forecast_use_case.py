"""Wiring the ForecastService from stored snapshots (P0-18)."""

import copy
from datetime import datetime, timedelta

import pytest
from capture import NOW, Provider, capture
from factories import live_context
from fakes import InMemoryIdMappingRepository, InMemorySnapshotRepository

from parkmind.core.contracts import PARK_TZ, AttractionStatus, DataSource, WaitEstimate
from parkmind.services.planning.forecast_service import (
    API_FORECAST,
    CACHED_SNAPSHOT,
    HISTORICAL_PROFILE,
)
from parkmind.services.use_cases.collect_snapshot import SnapshotCollector
from parkmind.services.use_cases.forecast import (
    build_forecast_service,
    build_wait_profile,
)

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


# --- build_forecast_service over the 2026-09-27 capture ------------------------------

LIVE = capture("themeparks_live.json")
FORECAST_IDS = sorted(e["id"] for e in LIVE["liveData"] if e.get("forecast"))
WAIT_ONLY_ID = next(  # an operating ride with a posted wait but no provider forecast
    e["id"]
    for e in LIVE["liveData"]
    if e["entityType"] == "ATTRACTION"
    and not e.get("forecast")
    and e.get("status") == "OPERATING"
    and ((e.get("queue") or {}).get("STANDBY") or {}).get("waitTime") is not None
)
SWISS_FAMILY = "30fe3c64-af71-4c66-a54b-aa61fd7af177"  # forecast 09:00 -> 10 min in the capture
PLAN_NOW = NOW + timedelta(minutes=5)
AT_14 = NOW.replace(hour=14, minute=15, second=0)


def _collected(live: dict | None = None) -> tuple[InMemorySnapshotRepository, InMemoryIdMappingRepository, str]:
    provider = Provider(live=live)
    snapshots, ids = InMemorySnapshotRepository(), InMemoryIdMappingRepository()
    result = SnapshotCollector(provider.parks(), provider.weather(), snapshots, ids).collect(now=NOW)
    return snapshots, ids, result.snapshot_id


def _history(snapshots: InMemorySnapshotRepository, attraction_id: str, wait: float) -> None:
    """Three earlier days with a reading at 14:xx, so the profile has the cell."""
    for days in (3, 4, 5):
        _store(snapshots, AT_14 - timedelta(days=days), {attraction_id: wait})


def test_capture_forecast_is_deterministic() -> None:
    def run() -> list[tuple]:
        snapshots, ids, _ = _collected()
        service = build_forecast_service(snapshots, ids, now=PLAN_NOW)
        return [service.forecast_wait(a, AT_14, now=PLAN_NOW) for a in FORECAST_IDS]

    first, second = run(), run()

    assert first == second
    assert len(FORECAST_IDS) == 26
    assert all(f is not None and f.strategy == API_FORECAST for f in first)


def test_api_forecast_comes_from_the_snapshot_raw_payload() -> None:
    snapshots, ids, sid = _collected()
    service = build_forecast_service(snapshots, ids, now=PLAN_NOW)

    forecast = service.forecast_wait(SWISS_FAMILY, NOW.replace(hour=9, minute=30), now=PLAN_NOW)

    assert forecast is not None
    assert (forecast.wait_minutes, forecast.strategy, forecast.data_source) == (
        10.0,
        API_FORECAST,
        DataSource.THEMEPARKS_WIKI,
    )
    assert (forecast.snapshot_id, forecast.as_of) == (sid, snapshots.get(sid).retrieved_at)  # type: ignore[union-attr]


def test_api_source_error_falls_back_to_historical_profile() -> None:
    snapshots, ids, sid = _collected()
    snapshots.rows[sid]["raw"] = {"raw_schema": 1}  # the stored /live is gone
    _history(snapshots, SWISS_FAMILY, 33.0)

    forecast = build_forecast_service(snapshots, ids, now=PLAN_NOW).forecast_wait(
        SWISS_FAMILY, AT_14, now=PLAN_NOW
    )

    assert forecast is not None
    assert (forecast.strategy, forecast.data_source, forecast.wait_minutes) == (
        HISTORICAL_PROFILE,
        DataSource.HISTORICAL,
        33.0,
    )


def test_malformed_raw_forecast_degrades_to_historical() -> None:
    live = copy.deepcopy(LIVE)
    entity = next(e for e in live["liveData"] if e["id"] == SWISS_FAMILY)
    entity["forecast"][0]["waitTime"] = "ten"
    snapshots, ids, _ = _collected(live)
    _history(snapshots, SWISS_FAMILY, 33.0)

    service = build_forecast_service(snapshots, ids, now=PLAN_NOW)

    assert API_FORECAST not in service.strategy_names
    forecast = service.forecast_wait(SWISS_FAMILY, AT_14, now=PLAN_NOW)
    assert forecast is not None and forecast.strategy == HISTORICAL_PROFILE


def test_two_provider_ids_on_one_internal_id_get_no_api_forecast(caplog: pytest.LogCaptureFixture) -> None:
    snapshots, ids, _ = _collected()
    kept, first, second = FORECAST_IDS[0], FORECAST_IDS[1], FORECAST_IDS[2]
    key = next(k for k in ids.rows if k[1] == second)
    ids.rows[key] = first  # a curated re-map gone wrong: two provider ids, one internal id

    with caplog.at_level("WARNING", logger="parkmind.services.use_cases.forecast"):
        service = build_forecast_service(snapshots, ids, now=PLAN_NOW)

    collided = service.forecast_wait(first, AT_14, now=PLAN_NOW)
    assert collided is None or collided.strategy != API_FORECAST
    assert service.forecast_wait(kept, AT_14, now=PLAN_NOW).strategy == API_FORECAST  # type: ignore[union-attr]
    assert any(first in r.getMessage() and second in r.getMessage() for r in caplog.records)


def test_no_profile_falls_back_to_latest_valid_snapshot_with_cache_provenance() -> None:
    snapshots, ids, sid = _collected()
    posted = snapshots.get(sid).waits[WAIT_ONLY_ID].wait_minutes  # type: ignore[union-attr]

    forecast = build_forecast_service(snapshots, ids, now=PLAN_NOW).forecast_wait(
        WAIT_ONLY_ID, AT_14, now=PLAN_NOW
    )

    assert forecast is not None
    assert (forecast.strategy, forecast.data_source, forecast.snapshot_id, forecast.wait_minutes) == (
        CACHED_SNAPSHOT,
        DataSource.CACHE,
        sid,
        posted,
    )


class _RawReadCounter(InMemorySnapshotRepository):
    """Counts raw payload reads: the costly step a stale snapshot must skip."""

    def __init__(self) -> None:
        super().__init__()
        self.raw_reads = 0

    def get_raw_payload(self, snapshot_id: str) -> dict | None:
        self.raw_reads += 1
        return super().get_raw_payload(snapshot_id)


def test_a_stale_snapshot_builds_no_snapshot_strategy_and_reads_no_raw_payload() -> None:
    snapshots, ids = _RawReadCounter(), InMemoryIdMappingRepository()
    provider = Provider()
    SnapshotCollector(provider.parks(), provider.weather(), snapshots, ids).collect(now=NOW)

    fresh = build_forecast_service(snapshots, ids, now=PLAN_NOW)
    assert (fresh.strategy_names, snapshots.raw_reads) == (
        (API_FORECAST, HISTORICAL_PROFILE, CACHED_SNAPSHOT),
        1,
    )

    stale = build_forecast_service(snapshots, ids, now=NOW + timedelta(hours=2))
    assert (stale.strategy_names, snapshots.raw_reads) == ((HISTORICAL_PROFILE,), 1)


def test_a_stale_snapshot_is_never_read_as_a_current_forecast() -> None:
    snapshots, ids, _ = _collected()
    late = NOW + timedelta(hours=2)  # the only snapshot is 2 h old: past rule 11's window
    service = build_forecast_service(snapshots, ids, now=late)

    assert service.strategy_names == (HISTORICAL_PROFILE,)
    assert service.forecast_wait(WAIT_ONLY_ID, AT_14, now=late) is None
    assert service.forecast_wait(FORECAST_IDS[0], AT_14, now=late) is None
    _history(snapshots, WAIT_ONLY_ID, 33.0)
    rebuilt = build_forecast_service(snapshots, ids, now=late).forecast_wait(WAIT_ONLY_ID, AT_14, now=late)
    assert rebuilt is not None and rebuilt.strategy == HISTORICAL_PROFILE


def test_no_snapshot_at_all_leaves_only_the_profile() -> None:
    service = build_forecast_service(
        InMemorySnapshotRepository(), InMemoryIdMappingRepository(), now=PLAN_NOW
    )

    assert service.strategy_names == (HISTORICAL_PROFILE,)
