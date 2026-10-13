"""Use case: judge a party's events, record them, and supersede a stale proposal
(sections 24-25, 9 [C18], 36; backlog P0-33).

The replanning graph's EVENT POLICY step (P0-34) calls this with what its
state holds -- the active plan, the constraints and the group objective --
because those live in the thread's checkpoints, not in a repository. The
park-wide candidates come from ``MonitorEventsUseCase.detect`` or from a
confirmed user report.

In one transaction:

1. the thread's recorded events, the plan's execution state, the catalog's
   outdoor flags and the guests' daily walking limits (from the SessionStore
   by ``accessibility_ref``, never from state [C19]) are read. A guest whose
   record cannot be read has an unknown limit: their fatigue report needs
   confirmation, as with a known limit, and ``requirements_missing`` says so;
2. ``BEHIND_SCHEDULE`` is derived from the execution state;
3. a user event that needs the guests' confirmation (section 25 [C10]) and
   arrives with ``confirmed`` False is set aside -- not recorded, not applied --
   and comes back in ``awaiting_confirmation`` for the CONFIRM interrupt. The
   rest of the batch goes on: a monitor closure is never held back by a
   doubtful report;
4. ``deduplicate`` drops what the thread already holds, whatever the source;
5. ``PARTY_RELOCATED`` is applied to the execution state, *then* the policy
   classifies (section 36);
6. every event is recorded -- only once classified, so a stored event always
   carries ``severity`` and ``requires_replan``;
7. the execution state is saved if the relocation or a walking cap changed it;
8. a PENDING proposal is superseded only when a *newly recorded* event
   requires a replan (section 9 [C18]); a duplicate or an unimportant change
   never does. No behavior entry is written for it.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from parkmind.core.contracts import (
    Event,
    GroupObjective,
    PartyConstraints,
    Plan,
    PlanExecutionState,
)
from parkmind.services.planning.event_dedup import deduplicate
from parkmind.services.planning.event_detector import detect_behind_schedule
from parkmind.services.planning.event_policy import (
    DEFAULT_POLICY_CONFIG,
    PartyEventContext,
    PolicyConfig,
    PolicyDecision,
    apply_decisions,
    apply_execution_updates,
    classify,
    needs_confirmation,
)
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    load_requirements,
    open_deps,
)

logger = logging.getLogger(__name__)


class UnclassifiedEventError(RuntimeError):
    """An event reached the repository without EventPolicy's severity and requires_replan."""


@dataclass(frozen=True)
class HandledEvents:
    decisions: list[PolicyDecision]
    """One per newly recorded event, with its effects."""
    duplicates: int
    """Candidates the thread already held."""
    superseded: list[str]
    """Proposal ids marked SUPERSEDED."""
    execution_state: PlanExecutionState | None
    requirements_missing: tuple[str, ...] = ()
    """Guests whose accessibility requirements could not be read (limits unknown)."""
    awaiting_confirmation: list[Event] = field(default_factory=list)
    """New user events set aside until the guests confirm them; nothing was written for them."""

    @property
    def requires_replan(self) -> bool:
        return any(d.event.requires_replan for d in self.decisions)

    @property
    def events(self) -> list[Event]:
        return [d.event for d in self.decisions]


class HandleEventsUseCase:
    def __init__(
        self,
        deps_factory: DepsFactory = default_planning_deps,
        *,
        config: PolicyConfig = DEFAULT_POLICY_CONFIG,
    ) -> None:
        self._deps_factory = deps_factory
        self._config = config

    def execute(
        self,
        thread_id: str,
        candidates: Sequence[Event],
        *,
        plan: Plan,
        constraints: PartyConstraints,
        objective: GroupObjective,
        accessibility_ref: Sequence[str],
        confirmed: bool,
        now: datetime,
    ) -> HandledEvents:
        """Judge ``candidates`` for the party whose active plan is ``plan``."""
        with open_deps(self._deps_factory) as deps, deps.transaction():
            history = deps.events.list_for_thread(thread_id)
            stored_state = deps.execution_states.get(plan.plan_id)
            outdoor = {a.node_id for a in load_catalog(deps) if a.outdoor}
            requirements, missing = load_requirements(
                deps, thread_id, accessibility_ref
            )
            if missing:
                logger.warning(
                    "thread %s: accessibility requirements of %s could not be read; "
                    "their fatigue reports need confirmation",
                    thread_id,
                    ", ".join(missing),
                )
            limits = {
                r.guest_id: r.daily_walking_limit_minutes
                for r in requirements
                if r.daily_walking_limit_minutes is not None
            }

            behind = detect_behind_schedule(
                plan,
                stored_state,
                now=now,
                min_minutes_behind=self._config.behind_schedule_minutes,
            )
            pool = [*candidates, *([behind] if behind is not None else [])]
            held: list[Event] = []
            ready: list[Event] = []
            for event in pool:
                gate = needs_confirmation(
                    event,
                    thresholds=objective.event_thresholds,
                    daily_walking_limits=limits,
                    unread_requirements=missing,
                    config=self._config,
                )
                (held if gate and not confirmed else ready).append(event)
            new = deduplicate(ready, history)
            # A report of something the thread already holds needs no question.
            awaiting = deduplicate(held, [*history, *new])

            state = apply_execution_updates(
                stored_state, new, plan_id=plan.plan_id, now=now
            )
            decisions = classify(
                new,
                party=PartyEventContext(
                    plan=plan,
                    must_do=constraints.must_do,
                    thresholds=objective.event_thresholds,
                    outdoor_ids=outdoor,
                    execution_state=state,
                    daily_walking_limits=limits,
                    unread_requirements=missing,
                ),
                confirmed=confirmed,
                config=self._config,
            )
            state = apply_decisions(state, decisions, plan_id=plan.plan_id, now=now)

            recorded: list[PolicyDecision] = []
            for decision in decisions:
                event = decision.event
                if event.severity is None or event.requires_replan is None:
                    raise UnclassifiedEventError(f"{event.event_id} was not classified")
                if deps.events.record(thread_id, event):
                    recorded.append(decision)

            if state is not None and state != stored_state:
                deps.execution_states.save(state)

            superseded = (
                deps.proposals.supersede_pending(thread_id, at=now)
                if any(d.event.requires_replan for d in recorded)
                else []
            )
        return HandledEvents(
            decisions=recorded,
            duplicates=len(pool) - len(new) - len(awaiting),
            superseded=superseded,
            execution_state=state,
            requirements_missing=tuple(missing),
            awaiting_confirmation=awaiting,
        )
