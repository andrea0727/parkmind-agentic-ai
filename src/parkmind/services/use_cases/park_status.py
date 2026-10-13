"""Use case: is the park still operating today?

Nothing can be scheduled once the park has closed, so planning stops there with a plain
message instead of the checker's "outside operating hours" violations. A schedule that
cannot be read is not a reason to refuse: ``None`` leaves the decision to the planner.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from parkmind.core.contracts import PARK_TZ
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    PlanningUnavailableError,
    default_planning_deps,
    load_park,
    open_deps,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParkStatus:
    closed: bool
    opening_time: datetime
    closing_time: datetime

    def closing_label(self) -> str:
        return self.closing_time.astimezone(PARK_TZ).strftime("%H:%M")


class ParkClosedError(PlanningUnavailableError):
    """The park has closed for the day: no plan can be built until it reopens."""

    def __init__(self, status: ParkStatus) -> None:
        super().__init__(f"the park closed at {status.closing_label()} (park time)")
        self.status = status


class ParkStatusUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(self, now: datetime) -> ParkStatus | None:
        try:
            with open_deps(self._deps_factory) as deps:
                park = load_park(deps, now)
        except PlanningUnavailableError:
            logger.warning("park status unknown: no schedule", exc_info=True)
            return None
        return ParkStatus(now >= park.closing_time, park.opening_time, park.closing_time)

    def ensure_open(self, now: datetime) -> None:
        status = self.execute(now)
        if status is not None and status.closed:
            raise ParkClosedError(status)
