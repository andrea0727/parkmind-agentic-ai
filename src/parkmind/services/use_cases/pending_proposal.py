"""Use case: the proposal a thread's guests are still deciding on (P0-34).

The proposal repository, not graph state, is the source of truth for what is waiting for
a human: a run of another graph on the same thread does not carry the interrupt of the
run it follows, so the replanning graph reads it from here to put it back in front of
the guests.
"""

from dataclasses import dataclass

from parkmind.core.contracts import Plan, Proposal
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    open_deps,
)


@dataclass(frozen=True)
class PendingProposal:
    proposal: Proposal
    candidate_plan: Plan


class PendingProposalUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(self, thread_id: str) -> PendingProposal | None:
        """The newest PENDING proposal with its plan, or ``None``."""
        with open_deps(self._deps_factory) as deps:
            for proposal in reversed(deps.proposals.list_pending(thread_id)):
                plan = deps.plans.get(proposal.candidate_plan_id)
                if plan is not None:
                    return PendingProposal(proposal, plan)
        return None
