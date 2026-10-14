"""Use case: record that the party completed or skipped a stop of its active plan (P1-01).

One report updates two aggregates in one transaction (``PlanExecutionState`` and the
``BehaviorLog``, section 33 [C12, C13]):
- the stored progress: the stop joins ``completed_stop_ids`` or
  ``skipped_stop_ids``. That progress is what the replanner keeps, and what
  ``BEHIND_SCHEDULE`` reads (P0-33). Once a stop is reported, the stored progress
  is the reported one: replanning no longer assumes an on-schedule party
  (``replan.effective_execution_state``);
- for an attraction or show, an ``ATTRACTION_COMPLETED`` / ``ATTRACTION_SKIPPED``
  entry for each guest the stop served (``Stop.served_guests``, empty meaning the
  whole party). The entries are keyed by ``party_guest_id``, with the guest's role
  only, like the proposal decisions. Meal and rest stops change the progress only:
  they say nothing about what a guest likes.

The report is idempotent: the same outcome reported twice changes nothing and logs
once (``entry_id`` is the plan, the stop and the outcome). A stop can't be both
completed and skipped, and only a stop of the thread's *active* plan can be
reported: a candidate is not being walked yet. Walking minutes are not part of a
report; they stay with the events that carry them (P0-33).

No API exposes this yet (P0-35b).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from parkmind.core.contracts import (
    BehaviorEntry,
    BehaviorEventType,
    Guest,
    PlanExecutionState,
    StopKind,
)
from parkmind.services.ports import party_guest_id
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    open_deps,
)

_ATTRACTION_KINDS = frozenset({StopKind.ATTRACTION, StopKind.SHOW})


class StopOutcome(str, Enum):
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


_EVENT_TYPE = {
    StopOutcome.COMPLETED: BehaviorEventType.ATTRACTION_COMPLETED,
    StopOutcome.SKIPPED: BehaviorEventType.ATTRACTION_SKIPPED,
}


class ProgressProblem(str, Enum):
    NOT_THE_ACTIVE_PLAN = "NOT_THE_ACTIVE_PLAN"
    NOT_IN_PLAN = "NOT_IN_PLAN"
    OTHER_OUTCOME_RECORDED = "OTHER_OUTCOME_RECORDED"


class InvalidProgressError(ValueError):
    """The report can't be recorded; nothing was changed."""

    def __init__(self, problem: ProgressProblem, message: str) -> None:
        self.problem = problem
        super().__init__(message)


@dataclass(frozen=True)
class RecordedProgress:
    execution_state: PlanExecutionState
    """The plan's stored progress after the report."""
    changed: bool
    """``False`` when this outcome was already recorded for the stop."""


class RecordProgressUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(
        self,
        thread_id: str,
        plan_id: str,
        node_id: str,
        outcome: StopOutcome,
        *,
        at: datetime,
        guests: Sequence[Guest],
    ) -> RecordedProgress:
        """``guests`` is the party; ``node_id`` names a stop of the active plan ``plan_id``."""
        if not guests:
            raise ValueError(
                "progress is logged per guest: the party's guests are required"
            )
        with open_deps(self._deps_factory) as deps, deps.transaction():
            active = deps.plans.get_active(thread_id)
            if active is None or active.plan_id != plan_id:
                raise InvalidProgressError(
                    ProgressProblem.NOT_THE_ACTIVE_PLAN,
                    f"{plan_id} is not the active plan of this thread",
                )
            stop = next((s for s in active.stops if s.node_id == node_id), None)
            if stop is None:
                raise InvalidProgressError(
                    ProgressProblem.NOT_IN_PLAN, f"{node_id} is not a stop of {plan_id}"
                )

            state = deps.execution_states.get(plan_id) or PlanExecutionState(
                plan_id=plan_id, as_of=at
            )
            completed = outcome is StopOutcome.COMPLETED
            recorded = state.completed_stop_ids if completed else state.skipped_stop_ids
            other = state.skipped_stop_ids if completed else state.completed_stop_ids
            if node_id in other:
                raise InvalidProgressError(
                    ProgressProblem.OTHER_OUTCOME_RECORDED,
                    f"{node_id} is already recorded as "
                    f"{'skipped' if completed else 'completed'}",
                )
            changed = node_id not in recorded
            if changed:
                field = "completed_stop_ids" if completed else "skipped_stop_ids"
                # A late report never moves the progress back in time (the repository
                # keeps the newer state).
                state = state.model_copy(
                    update={field: [*recorded, node_id], "as_of": max(state.as_of, at)}
                )
                deps.execution_states.save(state)

            if stop.kind in _ATTRACTION_KINDS:
                entry = BehaviorEntry(
                    entry_id=f"{plan_id}:{node_id}:{outcome.value}",
                    event_type=_EVENT_TYPE[outcome],
                    plan_id=plan_id,
                    attraction_ids=[node_id],
                    timestamp=at,
                )
                served = set(stop.served_guests)
                for guest in guests:
                    if served and guest.guest_id not in served:
                        continue
                    scoped = party_guest_id(thread_id, guest.guest_id)
                    deps.guests.save(Guest(guest_id=scoped, role=guest.role))
                    deps.behavior.append(scoped, entry)
        return RecordedProgress(state, changed)
