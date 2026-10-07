"""ForecastStrategy -- one source of expected wait times (section 18, P0-18).

``ForecastService`` (services/planning/forecast_service.py) is the section 18
composite: it asks its strategies in order -- API forecast, then historical
profile, then the recent cached snapshot (section 43) -- and returns the first
answer. Each answer is a ``WaitForecast`` that says which strategy produced it
and from what data, so a plan's provenance can answer "where did this wait come
from?" (section 32) without the LLM's memory.

``WaitForecast`` is a services-level result type, not a section 33 contract: it
never enters LangGraph state or a stored plan; what reaches the plan is the
strategy label and data sources in ``Provenance``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from parkmind.core.contracts import DataSource


@dataclass(frozen=True)
class WaitForecast:
    """The standby wait expected at ``attraction_id`` at ``at``."""

    attraction_id: str
    at: datetime
    wait_minutes: float
    strategy: str
    """Name of the strategy that answered (e.g. ``"api_forecast"``)."""
    data_source: DataSource
    snapshot_id: str | None
    """Snapshot the reading came from; ``None`` for an aggregate (historical profile)."""
    as_of: datetime
    """When the underlying data was retrieved (or, for a profile, built)."""


class ForecastStrategy(Protocol):
    @property
    def name(self) -> str:
        """Strategy name recorded in provenance."""
        ...

    def forecast(self, attraction_id: str, at: datetime, *, now: datetime) -> WaitForecast | None:
        """The expected wait at ``at``, or ``None`` if this strategy has no reading.

        ``now`` is the caller's clock: a strategy never reads the system clock,
        and uses ``now`` only to refuse readings that are too old to be current.
        Raises ``ForecastSourceError`` when its source failed or is malformed,
        so the service can fall through to the next strategy.
        """
        ...
