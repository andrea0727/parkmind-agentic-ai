"""The park snapshot to answer from at ``now`` (Architecture section 43, P0-11).

One function, shared by LOAD CONTEXT (``load_context``) and the ``data.*`` tools
(``park_data_queries``), so both read the same snapshot the same way.
"""

from datetime import datetime, timedelta
from typing import Literal

from parkmind.core.contracts import LiveContext
from parkmind.services.clients.themeparks_errors import ThemeParksClientError
from parkmind.services.ports import RepositoryError
from parkmind.services.use_cases.latest_snapshot import (
    DEFAULT_MAX_AGE,
    latest_valid_snapshot,
)
from parkmind.services.use_cases.planning_deps import (
    ContextUnavailableError,
    PlanningDeps,
)

ContextSource = Literal["live", "snapshot"]
"""``live`` when this call collected (or found this window's) snapshot; ``snapshot`` for the fallback."""


def current_snapshot(
    deps: PlanningDeps, now: datetime, *, max_age: timedelta = DEFAULT_MAX_AGE
) -> tuple[LiveContext, ContextSource]:
    """A fresh collection when a collector is configured and the provider answers,
    else the latest valid stored snapshot -- returned even when it is older than
    ``max_age``: rule 11 (and the data tools' ``stale`` flag) judge its age, the
    loader does not hide it. ``ContextUnavailableError`` when there is neither.
    """
    if deps.collector is not None:
        try:
            result = deps.collector.collect(now=now)
            live = result.live_context or deps.snapshots.get(result.snapshot_id)
            if live is not None:
                return live, "live"
        except (ThemeParksClientError, RepositoryError):
            pass  # fall back to the stored snapshot (section 43)
    latest = latest_valid_snapshot(deps.snapshots, now=now, max_age=max_age)
    if latest is None:
        raise ContextUnavailableError("no live data and no valid stored snapshot")
    return latest.live_context, "snapshot"
