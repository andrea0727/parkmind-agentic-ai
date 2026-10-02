"""ForecastService -- expected standby waits for a stop's time, with fallback (section 18, P0-18).

A plan places each stop at a future time, so the queue the optimizer charges
(section 19, ``lambda_q * queue``) should be the one expected *then*, not the one
posted now. This module is the section 18 composite: ``ForecastService`` asks
its strategies in order and returns the first reading, and every reading says
which strategy produced it, from what data, and when (section 32).

The chain the composer wires (``services/use_cases/forecast.py``) is section
43's: API forecast -> historical profile -> recent cached snapshot.

- ``ApiForecastStrategy``: the provider's hourly forecast, read from the raw
  ``/live`` payload of the snapshot the plan is built from (same ``snapshot_id``).
- ``HistoricalProfileStrategy``: the median standby wait per (attraction,
  park-local hour) over our own stored snapshots -- section 18's "historical
  profiles are built from our own Postgres snapshots".
- ``CachedSnapshotStrategy``: the snapshot's current wait, used as-is.

Freshness: a reading taken from a snapshot is used only while that snapshot is
fresh at ``now`` (``0 <= now - retrieved_at <= max_age``), the same window rule 11
applies, so a stale reading is never served as a current forecast. A profile is
an aggregate, not a reading, and has no such window. Per-reading freshness
(an entity's own ``lastUpdated`` instead of the snapshot's age) is a pending
decision in the backlog and is deliberately not decided here.

No ML (section 14.1), no I/O, no clock: ``now`` is always the caller's.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from parkmind.core.contracts import PARK_TZ, AttractionStatus, DataSource, LiveContext
from parkmind.services.ports import ForecastSourceError, ForecastStrategy, WaitForecast

logger = logging.getLogger(__name__)

API_FORECAST = "api_forecast"
HISTORICAL_PROFILE = "historical_profile"
CACHED_SNAPSHOT = "cached_snapshot"

ForecastPoints = Sequence[tuple[datetime, float | None]]
"""Hourly ``(hour start, wait)`` pairs; ``None`` is an hour with no reading."""


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def is_fresh(retrieved_at: datetime, *, now: datetime, max_age: timedelta) -> bool:
    """``True`` when data retrieved at ``retrieved_at`` is current at ``now``.

    A snapshot dated after ``now`` is not trusted as fresh, as in
    ``latest_valid_snapshot`` and rule 11.
    """
    return timedelta(0) <= now - retrieved_at <= max_age


class ForecastService:
    """Section 18's composite: the first strategy with a reading answers."""

    def __init__(self, strategies: Sequence[ForecastStrategy]) -> None:
        self._strategies = tuple(strategies)

    @property
    def strategy_names(self) -> tuple[str, ...]:
        """The strategies, in the order they are asked."""
        return tuple(s.name for s in self._strategies)

    def forecast_wait(
        self, attraction_id: str, at: datetime, *, now: datetime
    ) -> WaitForecast | None:
        """The expected standby wait at ``attraction_id`` at ``at``.

        Each strategy is asked in order; one with no reading (``None``) or a
        failed source (``ForecastSourceError``) passes to the next. ``None`` when
        no strategy has a reading: the caller decides what that means, nothing
        here invents a wait.
        """
        _require_aware(at, "at")
        _require_aware(now, "now")
        for strategy in self._strategies:
            try:
                forecast = strategy.forecast(attraction_id, at, now=now)
            except ForecastSourceError as exc:
                logger.warning("forecast strategy %s failed; falling back: %s", strategy.name, exc)
                continue
            if forecast is not None:
                return forecast
        return None


def forecast_strategy_label(forecasts: Iterable[WaitForecast]) -> str:
    """``Provenance.forecast_strategy`` for a set of forecasts.

    The distinct strategy names, sorted and joined with ``+`` (e.g.
    ``"api_forecast+cached_snapshot"``), so the label is deterministic; ``"none"``
    when nothing was forecast.
    """
    names = sorted({f.strategy for f in forecasts})
    return "+".join(names) if names else "none"


def forecast_data_sources(forecasts: Iterable[WaitForecast]) -> list[DataSource]:
    """The distinct data sources behind a set of forecasts, for ``Provenance.data_sources``."""
    return sorted({f.data_source for f in forecasts}, key=lambda s: s.value)


class ApiForecastStrategy:
    """The provider's hourly forecast, taken from one snapshot's raw ``/live`` payload."""

    name = API_FORECAST

    def __init__(
        self,
        points: Mapping[str, ForecastPoints],
        *,
        snapshot_id: str,
        retrieved_at: datetime,
        max_age: timedelta,
    ) -> None:
        _require_aware(retrieved_at, "retrieved_at")
        self._points = {
            attraction_id: tuple(sorted(series, key=lambda p: p[0]))
            for attraction_id, series in points.items()
        }
        self._snapshot_id = snapshot_id
        self._retrieved_at = retrieved_at
        self._max_age = max_age

    def forecast(self, attraction_id: str, at: datetime, *, now: datetime) -> WaitForecast | None:
        """The point whose hour contains ``at``; ``None`` outside the forecast's hours,
        for an hour without a reading, or once the snapshot is stale at ``now``."""
        if not is_fresh(self._retrieved_at, now=now, max_age=self._max_age):
            return None
        series = self._points.get(attraction_id, ())
        for i, (start, wait) in enumerate(series):
            end = series[i + 1][0] if i + 1 < len(series) else start + timedelta(hours=1)
            if start <= at < end:
                if wait is None:
                    return None
                return WaitForecast(
                    attraction_id=attraction_id,
                    at=at,
                    wait_minutes=wait,
                    strategy=self.name,
                    data_source=DataSource.THEMEPARKS_WIKI,
                    snapshot_id=self._snapshot_id,
                    as_of=self._retrieved_at,
                )
        return None


@dataclass(frozen=True)
class WaitProfile:
    """Median standby wait per ``(attraction_id, park-local hour)``.

    Built by ``services/use_cases/forecast.build_wait_profile`` from stored
    snapshots; only cells with enough samples are present.
    """

    medians: Mapping[tuple[str, int], float]
    built_at: datetime
    sample_counts: Mapping[tuple[str, int], int] = field(default_factory=dict)


class HistoricalProfileStrategy:
    """The historical median for the attraction at ``at``'s park-local hour."""

    name = HISTORICAL_PROFILE

    def __init__(self, profile: WaitProfile) -> None:
        _require_aware(profile.built_at, "profile.built_at")
        self._profile = profile

    def forecast(self, attraction_id: str, at: datetime, *, now: datetime) -> WaitForecast | None:
        median = self._profile.medians.get((attraction_id, at.astimezone(PARK_TZ).hour))
        if median is None:
            return None
        return WaitForecast(
            attraction_id=attraction_id,
            at=at,
            wait_minutes=median,
            strategy=self.name,
            data_source=DataSource.HISTORICAL,
            snapshot_id=None,
            as_of=self._profile.built_at,
        )


class CachedSnapshotStrategy:
    """The snapshot's current standby wait (section 43's "recent cached snapshot").

    Only while the snapshot is fresh at ``now``, and only for an OPERATING
    attraction: the posted wait of a DOWN or CLOSED ride is not a forecast
    (whether the stop is allowed at all is rule 1's call, not this one's).
    """

    name = CACHED_SNAPSHOT

    def __init__(self, live_context: LiveContext, *, max_age: timedelta) -> None:
        self._live_context = live_context
        self._max_age = max_age

    def forecast(self, attraction_id: str, at: datetime, *, now: datetime) -> WaitForecast | None:
        context = self._live_context
        if not is_fresh(context.retrieved_at, now=now, max_age=self._max_age):
            return None
        estimate = context.waits.get(attraction_id)
        if estimate is None or estimate.status != AttractionStatus.OPERATING:
            return None
        return WaitForecast(
            attraction_id=attraction_id,
            at=at,
            wait_minutes=estimate.wait_minutes,
            strategy=self.name,
            data_source=DataSource.CACHE,
            snapshot_id=context.snapshot_id,
            as_of=context.retrieved_at,
        )
