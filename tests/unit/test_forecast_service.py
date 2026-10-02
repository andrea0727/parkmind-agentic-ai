"""ForecastStrategy port and ForecastService (P0-18)."""

import ast
import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from factories import live_context, provenance

from parkmind.core.contracts import PARK_TZ, AttractionStatus, DataSource, WaitEstimate
from parkmind.services import ports
from parkmind.services.planning.forecast_service import (
    API_FORECAST,
    CACHED_SNAPSHOT,
    HISTORICAL_PROFILE,
    ApiForecastStrategy,
    CachedSnapshotStrategy,
    ForecastService,
    HistoricalProfileStrategy,
    WaitProfile,
    forecast_data_sources,
    forecast_strategy_label,
)
from parkmind.services.ports import ForecastSourceError, ForecastStrategy, WaitForecast

SRC = Path(__file__).resolve().parents[2] / "src" / "parkmind"
RETRIEVED = datetime(2026, 9, 27, 11, 0, tzinfo=PARK_TZ)
NOW = RETRIEVED + timedelta(minutes=5)
MAX_AGE = timedelta(minutes=30)
AT_14 = datetime(2026, 9, 27, 14, 20, tzinfo=PARK_TZ)


def _accepts_port(strategy: ForecastStrategy) -> ForecastStrategy:
    return strategy


def _api(points: dict | None = None, *, retrieved_at: datetime = RETRIEVED) -> ApiForecastStrategy:
    hours = [datetime(2026, 9, 27, h, 0, tzinfo=PARK_TZ) for h in range(9, 18)]
    default = {"a1": [(start, 10.0 + start.hour) for start in hours]}
    return ApiForecastStrategy(
        default if points is None else points,
        snapshot_id="snap-api",
        retrieved_at=retrieved_at,
        max_age=MAX_AGE,
    )


def _profile(medians: dict | None = None) -> HistoricalProfileStrategy:
    return HistoricalProfileStrategy(
        WaitProfile(medians={("a1", 14): 42.0} if medians is None else medians, built_at=NOW)
    )


def _cached(*, retrieved_at: datetime = RETRIEVED, status: AttractionStatus = AttractionStatus.OPERATING):
    waits = {"a1": WaitEstimate(attraction_id="a1", wait_minutes=25.0, status=status)}
    return CachedSnapshotStrategy(
        live_context(snapshot_id="snap-cache", retrieved_at=retrieved_at, waits=waits),
        max_age=MAX_AGE,
    )


class _Fake:
    """A strategy that answers, misses or fails, and records that it was asked."""

    def __init__(self, name: str, result: float | None | Exception) -> None:
        self.name = name
        self.result = result
        self.calls = 0

    def forecast(self, attraction_id: str, at: datetime, *, now: datetime) -> WaitForecast | None:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        if self.result is None:
            return None
        return WaitForecast(attraction_id, at, self.result, self.name, DataSource.CACHE, None, now)


# --- the port ----------------------------------------------------------------------


def test_forecast_port_is_exported_from_ports() -> None:
    assert {"ForecastStrategy", "WaitForecast", "ForecastSourceError"} <= set(ports.__all__)
    assert ports.ForecastStrategy.__module__ == "parkmind.services.ports.forecast"
    assert ForecastSourceError.__module__ == "parkmind.services.ports.errors"


def test_wait_forecast_is_immutable() -> None:
    forecast = WaitForecast("a1", AT_14, 20.0, API_FORECAST, DataSource.THEMEPARKS_WIKI, "s", NOW)

    with pytest.raises(dataclasses.FrozenInstanceError):
        forecast.wait_minutes = 5.0  # type: ignore[misc]


# --- Done-when: stable interface ---------------------------------------------------


def test_forecast_service_satisfies_port_and_returns_wait_forecast() -> None:
    strategies = [_accepts_port(s) for s in (_api(), _profile(), _cached())]
    service = ForecastService(strategies)

    forecast = service.forecast_wait("a1", AT_14, now=NOW)

    assert service.strategy_names == (API_FORECAST, HISTORICAL_PROFILE, CACHED_SNAPSHOT)
    assert isinstance(forecast, WaitForecast)
    assert (forecast.attraction_id, forecast.at, forecast.wait_minutes) == ("a1", AT_14, 24.0)


def test_strategies_are_tried_in_order() -> None:
    first, second, third = _Fake("first", None), _Fake("second", 7.0), _Fake("third", 9.0)

    forecast = ForecastService([first, second, third]).forecast_wait("a1", AT_14, now=NOW)

    assert forecast is not None and forecast.strategy == "second"
    assert (first.calls, second.calls, third.calls) == (1, 1, 0)


def test_a_failed_source_falls_through_to_the_next_strategy() -> None:
    broken, backup = _Fake("broken", ForecastSourceError("payload drift")), _Fake("backup", 5.0)

    forecast = ForecastService([broken, backup]).forecast_wait("a1", AT_14, now=NOW)

    assert forecast is not None and forecast.strategy == "backup"


def test_no_reading_anywhere_is_an_explicit_none() -> None:
    service = ForecastService([_api({}), _profile({}), _cached()])

    assert service.forecast_wait("unknown", AT_14, now=NOW) is None
    assert ForecastService([]).forecast_wait("a1", AT_14, now=NOW) is None


# --- Done-when: strategy recorded in provenance -----------------------------------


def test_each_forecast_records_strategy_source_and_snapshot() -> None:
    api = _api().forecast("a1", AT_14, now=NOW)
    historical = _profile().forecast("a1", AT_14, now=NOW)
    cached = _cached().forecast("a1", AT_14, now=NOW)

    assert api == WaitForecast("a1", AT_14, 24.0, API_FORECAST, DataSource.THEMEPARKS_WIKI, "snap-api", RETRIEVED)
    assert historical == WaitForecast("a1", AT_14, 42.0, HISTORICAL_PROFILE, DataSource.HISTORICAL, None, NOW)
    assert cached == WaitForecast("a1", AT_14, 25.0, CACHED_SNAPSHOT, DataSource.CACHE, "snap-cache", RETRIEVED)


def test_forecast_strategy_label_feeds_provenance() -> None:
    forecasts = [
        _cached().forecast("a1", AT_14, now=NOW),
        _api().forecast("a1", AT_14, now=NOW),
        _cached().forecast("a1", AT_14, now=NOW),
    ]
    readings = [f for f in forecasts if f is not None]

    record = provenance(
        forecast_strategy=forecast_strategy_label(readings),
        data_sources=forecast_data_sources(readings),
    )

    assert record.forecast_strategy == "api_forecast+cached_snapshot"
    assert record.data_sources == [DataSource.CACHE, DataSource.THEMEPARKS_WIKI]
    assert forecast_strategy_label([]) == "none"


# --- Done-when: a stale reading is never a current forecast -----------------------


def test_stale_snapshot_wait_is_never_a_current_forecast() -> None:
    stale_now = RETRIEVED + MAX_AGE + timedelta(seconds=1)

    assert _cached().forecast("a1", AT_14, now=RETRIEVED + MAX_AGE) is not None  # edge: still fresh
    assert _cached().forecast("a1", AT_14, now=stale_now) is None
    assert ForecastService([_cached()]).forecast_wait("a1", AT_14, now=stale_now) is None


def test_stale_snapshot_api_forecast_is_not_used() -> None:
    stale_now = RETRIEVED + timedelta(hours=2)

    forecast = ForecastService([_api(), _profile()]).forecast_wait("a1", AT_14, now=stale_now)

    assert forecast is not None and forecast.strategy == HISTORICAL_PROFILE


def test_future_dated_snapshot_is_not_fresh() -> None:
    before_retrieval = RETRIEVED - timedelta(minutes=1)

    assert _api().forecast("a1", AT_14, now=before_retrieval) is None
    assert _cached().forecast("a1", AT_14, now=before_retrieval) is None


# --- strategy details ---------------------------------------------------------------


def test_api_forecast_uses_the_hour_containing_at() -> None:
    api = _api()

    assert api.forecast("a1", datetime(2026, 9, 27, 9, 0, tzinfo=PARK_TZ), now=NOW).wait_minutes == 19.0  # type: ignore[union-attr]
    assert api.forecast("a1", datetime(2026, 9, 27, 17, 59, tzinfo=PARK_TZ), now=NOW).wait_minutes == 27.0  # type: ignore[union-attr]
    assert api.forecast("a1", datetime(2026, 9, 27, 18, 0, tzinfo=PARK_TZ), now=NOW) is None
    assert api.forecast("a1", datetime(2026, 9, 27, 8, 59, tzinfo=PARK_TZ), now=NOW) is None
    assert api.forecast("other", AT_14, now=NOW) is None


def test_api_forecast_hour_without_a_reading_falls_through() -> None:
    api = _api({"a1": [(datetime(2026, 9, 27, 14, 0, tzinfo=PARK_TZ), None)]})

    forecast = ForecastService([api, _profile()]).forecast_wait("a1", AT_14, now=NOW)

    assert forecast is not None and forecast.strategy == HISTORICAL_PROFILE


def test_profile_hour_is_park_local_for_a_utc_time() -> None:
    at_utc = datetime(2026, 9, 27, 18, 20, tzinfo=UTC)  # 14:20 in the park

    assert _profile().forecast("a1", at_utc, now=NOW).wait_minutes == 42.0  # type: ignore[union-attr]
    assert _api().forecast("a1", at_utc, now=NOW).wait_minutes == 24.0  # type: ignore[union-attr]


def test_cached_snapshot_ignores_rides_that_are_not_operating() -> None:
    assert _cached(status=AttractionStatus.DOWN).forecast("a1", AT_14, now=NOW) is None


# --- Done-when: `now` is a parameter; the clock is never read ----------------------


@pytest.mark.parametrize("field", ["at", "now"])
def test_forecast_requires_aware_now_and_at(field: str) -> None:
    kwargs = {"at": AT_14, "now": NOW}
    kwargs[field] = kwargs[field].replace(tzinfo=None)

    with pytest.raises(ValueError, match=f"{field} must be timezone-aware"):
        ForecastService([_cached()]).forecast_wait("a1", kwargs["at"], now=kwargs["now"])


_CLOCK_CALLS = {("datetime", "now"), ("datetime", "utcnow"), ("date", "today"), ("time", "time")}


@pytest.mark.parametrize(
    "module",
    [
        "services/planning/forecast_service.py",
        "services/ports/forecast.py",
        "services/use_cases/forecast.py",
    ],
)
def test_forecast_modules_never_read_the_clock(module: str) -> None:
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    calls = {
        (node.func.value.id, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }

    assert not calls & _CLOCK_CALLS
