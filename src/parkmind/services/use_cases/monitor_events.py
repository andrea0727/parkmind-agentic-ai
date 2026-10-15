"""Use case: the event monitor.

"The monitor runs independently of the agent" and "does not wake the LLM for
every snapshot": it compares the two most recent stored snapshots with the
deterministic ``event_detector`` and returns park-wide candidate events. The
snapshots themselves are taken by the collector (``scripts/run_monitor.py``
collects, then detects).

Candidates carry no ``severity`` or ``requires_replan``: those depend on the
party, so ``HandleEventsUseCase`` judges them per thread with EventPolicy.
Waking each thread's replanning graph is the replanning graph's job.

Per-reading freshness: reading times come from the current snapshot's raw
payload (``snapshot_normalization.reading_times``). A snapshot whose raw payload
can't be read gives no reading times, so no status or wait event -- the
detector fails closed rather than raising a false alarm. ``now`` is passed in;
nothing here reads the clock.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from parkmind.core.contracts import Event, LiveContext
from parkmind.services.planning.event_detector import (
    DEFAULT_DETECTOR_CONFIG,
    DetectorConfig,
    detect_events,
)
from parkmind.services.ports import SnapshotRepository, StoredDataError
from parkmind.services.use_cases.latest_snapshot import DEFAULT_LOOKBACK
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    open_deps,
)
from parkmind.services.use_cases.snapshot_normalization import (
    RawSnapshotError,
    reading_times,
)

logger = logging.getLogger(__name__)

BASELINE_SNAPSHOTS = 8
"""Newest snapshots read per run: the pair compared, plus the wait-spike baseline."""


@dataclass(frozen=True)
class MonitorResult:
    candidates: list[Event] = field(default_factory=list)
    current_snapshot_id: str | None = None
    previous_snapshot_id: str | None = None


class MonitorEventsUseCase:
    def __init__(
        self,
        deps_factory: DepsFactory = default_planning_deps,
        *,
        config: DetectorConfig = DEFAULT_DETECTOR_CONFIG,
        lookback: int = DEFAULT_LOOKBACK,
    ) -> None:
        self._deps_factory = deps_factory
        self._config = config
        self._lookback = lookback

    def detect(self, *, now: datetime) -> MonitorResult:
        """Candidate events between the two most recent valid snapshots, at ``now``."""
        with open_deps(self._deps_factory) as deps:
            latest = _latest_valid(deps.snapshots, self._lookback, BASELINE_SNAPSHOTS)
            if len(latest) < 2:
                return MonitorResult(
                    current_snapshot_id=latest[0].snapshot_id if latest else None
                )
            current, previous, *earlier = latest
            raw = deps.snapshots.get_raw_payload(current.snapshot_id)
            try:
                times = reading_times(raw, deps.id_mappings) if raw is not None else {}
            except RawSnapshotError as exc:
                logger.warning(
                    "snapshot %s has no readable reading times; no status events: %s",
                    current.snapshot_id,
                    exc,
                )
                times = {}
        return MonitorResult(
            candidates=detect_events(
                previous,
                current,
                now=now,
                reading_times=times,
                earlier=earlier,
                config=self._config,
            ),
            current_snapshot_id=current.snapshot_id,
            previous_snapshot_id=previous.snapshot_id,
        )


def _latest_valid(
    snapshots: SnapshotRepository, lookback: int, count: int
) -> list[LiveContext]:
    """The newest ``count`` snapshots that still validate, newest first.

    A row that no longer validates is skipped, as in ``latest_valid_snapshot``.
    """
    found: list[LiveContext] = []
    for snapshot_id in snapshots.recent_ids(lookback):
        try:
            context = snapshots.get(snapshot_id)
        except StoredDataError:
            logger.warning("snapshot %s no longer validates; skipped", snapshot_id)
            continue
        if context is not None:
            found.append(context)
        if len(found) == count:
            break
    return found
