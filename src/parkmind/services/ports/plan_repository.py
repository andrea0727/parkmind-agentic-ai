"""PlanRepository -- plan bodies and the *active* plan (section 33 ``Plan`` [C18]).

Active vs candidate state is persisted distinctly: ``save`` stores an immutable
plan body that is, by itself, only ever a candidate. A plan becomes active
solely through ``activate``, which requires an APPROVED proposal for it
(section 23: Approve -> active plan; Edit -> ``apply_edits`` -> checker -> a new
Proposal). Nothing in the LLM path can call it (invariant: the LLM never
activates a plan); the human-approval use case does.

``thread_id`` is a storage key, not part of the ``Plan`` contract: run
identifiers live on the run, not on the plan (section 33, C23).
"""

from datetime import datetime
from typing import Protocol

from parkmind.core.contracts import Plan


class PlanRepository(Protocol):
    def save(self, thread_id: str, plan: Plan) -> None:
        """Store ``plan`` (and its ``Provenance``) as a candidate body.

        Plan bodies are immutable: re-saving an identical plan is a no-op,
        saving a different body under the same ``plan_id`` raises
        ``PlanImmutableError``.
        """
        ...

    def get(self, plan_id: str) -> Plan | None: ...

    def activate(self, thread_id: str, plan_id: str, *, at: datetime) -> None:
        """Make ``plan_id`` the thread's active plan, replacing any previous one.

        Raises ``NotApprovedError`` unless a proposal for this plan in this
        thread is APPROVED; the check and the switch are one atomic step.
        """
        ...

    def get_active(self, thread_id: str) -> Plan | None: ...
