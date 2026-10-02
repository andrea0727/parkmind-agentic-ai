"""ForecastStrategy port and ForecastService (P0-18)."""

import dataclasses
from datetime import datetime

import pytest

from parkmind.core.contracts import PARK_TZ, DataSource
from parkmind.services import ports
from parkmind.services.ports import ForecastSourceError, WaitForecast

NOON = datetime(2026, 9, 27, 12, 0, tzinfo=PARK_TZ)


def test_forecast_port_is_exported_from_ports() -> None:
    assert {"ForecastStrategy", "WaitForecast", "ForecastSourceError"} <= set(ports.__all__)
    assert ports.ForecastStrategy.__module__ == "parkmind.services.ports.forecast"
    assert ForecastSourceError.__module__ == "parkmind.services.ports.errors"


def test_wait_forecast_is_immutable() -> None:
    forecast = WaitForecast(
        attraction_id="a1",
        at=NOON,
        wait_minutes=20.0,
        strategy="api_forecast",
        data_source=DataSource.THEMEPARKS_WIKI,
        snapshot_id="snap-1",
        as_of=NOON,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        forecast.wait_minutes = 5.0  # type: ignore[misc]
