"""In-memory fakes of repository ports, for unit tests that must not touch a DB.

They keep the Postgres adapters' documented semantics (idempotent save,
conflicting id re-map raises, re-normalization keeps retrieved_at), and
tests/integration/postgres/ runs the same use cases against the real adapters.
Only ports are faked -- never the core.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from parkmind.core.contracts import (
    ApprovalStatus,
    DataSource,
    LiveContext,
    Plan,
    Proposal,
    RejectionReason,
)
from parkmind.services.ports import (
    IdMappingConflictError,
    InvalidStateTransitionError,
    NotApprovedError,
    NotFoundError,
    PendingProposalExistsError,
    PlanImmutableError,
    SnapshotMeta,
)


class InMemoryIdMappingRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], str] = {}

    def record(
        self,
        provider: str,
        provider_id: str,
        entity_kind: str,
        internal_id: str,
        *,
        seen_at: datetime,
    ) -> None:
        key = (provider, provider_id, str(entity_kind))
        if self.rows.setdefault(key, internal_id) != internal_id:
            raise IdMappingConflictError("provider id already mapped elsewhere")

    def resolve(self, provider: str, provider_id: str, entity_kind: str) -> str | None:
        return self.rows.get((provider, provider_id, str(entity_kind)))

    def provider_ids_for(self, internal_id: str) -> list[tuple[str, str, str]]:
        return sorted(k for k, v in self.rows.items() if v == internal_id)


class InMemorySnapshotRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def save(
        self,
        snapshot: LiveContext,
        raw_payload: Any,
        data_sources: Sequence[DataSource],
        *,
        normalizer_version: int,
    ) -> bool:
        if snapshot.snapshot_id in self.rows:
            return False
        self.rows[snapshot.snapshot_id] = {
            "live_context": snapshot,
            "raw": dict(raw_payload),
            "sources": list(data_sources),
            "version": normalizer_version,
        }
        return True

    def replace_normalized(
        self, snapshot: LiveContext, *, normalizer_version: int
    ) -> bool:
        row = self.rows.get(snapshot.snapshot_id)
        if row is None:
            return False
        if row["live_context"].retrieved_at != snapshot.retrieved_at:
            raise ValueError("re-normalizing must keep the stored retrieved_at")
        row["live_context"], row["version"] = snapshot, normalizer_version
        return True

    def get(self, snapshot_id: str) -> LiveContext | None:
        row = self.rows.get(snapshot_id)
        return None if row is None else row["live_context"]

    def get_meta(self, snapshot_id: str) -> SnapshotMeta | None:
        row = self.rows.get(snapshot_id)
        if row is None:
            return None
        return SnapshotMeta(
            snapshot_id=snapshot_id,
            retrieved_at=row["live_context"].retrieved_at,
            data_sources=row["sources"],
            normalizer_version=row["version"],
        )

    def get_raw_payload(self, snapshot_id: str) -> Any:
        row = self.rows.get(snapshot_id)
        return None if row is None else row["raw"]

    def get_data_sources(self, snapshot_id: str) -> list[DataSource] | None:
        row = self.rows.get(snapshot_id)
        return None if row is None else row["sources"]

    def get_latest(self) -> LiveContext | None:
        ids = self.recent_ids(1)
        return self.get(ids[0]) if ids else None

    def recent_ids(self, limit: int) -> list[str]:
        ordered = sorted(
            self.rows,
            key=lambda sid: (self.rows[sid]["live_context"].retrieved_at, sid),
            reverse=True,
        )
        return ordered[: max(limit, 0)]

    def ids_below_version(self, normalizer_version: int) -> list[str]:
        return sorted(
            (
                sid
                for sid, row in self.rows.items()
                if row["version"] < normalizer_version
            ),
            key=lambda sid: (self.rows[sid]["live_context"].retrieved_at, sid),
        )


class InMemoryPlanRepository:
    """PlanRepository over a proposal repository, with ``activate`` gated on an APPROVED proposal."""

    def __init__(self, proposals: "InMemoryProposalRepository") -> None:
        self._proposals = proposals
        self.plans: dict[str, Plan] = {}
        self.active: dict[str, str] = {}

    def save(self, thread_id: str, plan: Plan) -> None:
        if self.plans.setdefault(plan.plan_id, plan) != plan:
            raise PlanImmutableError("plan bodies are immutable")

    def get(self, plan_id: str) -> Plan | None:
        return self.plans.get(plan_id)

    def activate(self, thread_id: str, plan_id: str, *, at: datetime) -> None:
        if plan_id not in self.plans:
            raise NotFoundError("plan does not exist")
        approved = any(
            p.candidate_plan_id == plan_id and p.approval_status is ApprovalStatus.APPROVED
            for t, p in self._proposals.rows.values()
            if t == thread_id
        )
        if not approved:
            raise NotApprovedError("no APPROVED proposal for this plan in this thread")
        self.active[thread_id] = plan_id

    def get_active(self, thread_id: str) -> Plan | None:
        plan_id = self.active.get(thread_id)
        return self.plans[plan_id] if plan_id else None


class InMemoryProposalRepository:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, Proposal]] = {}

    def save(self, thread_id: str, proposal: Proposal) -> None:
        if proposal.approval_status is not ApprovalStatus.PENDING:
            raise InvalidStateTransitionError("a proposal is created PENDING")
        if proposal.proposal_id in self.rows:
            return
        if self.list_pending(thread_id):
            raise PendingProposalExistsError("the thread already holds a PENDING proposal")
        self.rows[proposal.proposal_id] = (thread_id, proposal)

    def get(self, proposal_id: str) -> Proposal | None:
        row = self.rows.get(proposal_id)
        return row[1] if row else None

    def list_pending(self, thread_id: str) -> list[Proposal]:
        return [
            p
            for t, p in self.rows.values()
            if t == thread_id and p.approval_status is ApprovalStatus.PENDING
        ]

    def resolve(
        self,
        proposal_id: str,
        status: ApprovalStatus,
        *,
        at: datetime,
        rejection_reason: RejectionReason | None = None,
    ) -> Proposal:
        row = self.rows.get(proposal_id)
        if row is None:
            raise NotFoundError("proposal does not exist")
        thread_id, proposal = row
        if proposal.approval_status is not ApprovalStatus.PENDING or status is ApprovalStatus.PENDING:
            raise InvalidStateTransitionError("only a PENDING proposal can be resolved")
        resolved = proposal.model_copy(
            update={"approval_status": status, "rejection_reason": rejection_reason}
        )
        self.rows[proposal_id] = (thread_id, resolved)
        return resolved

    def supersede_pending(self, thread_id: str, *, at: datetime) -> list[str]:
        ids = [p.proposal_id for p in self.list_pending(thread_id)]
        for proposal_id in ids:
            self.resolve(proposal_id, ApprovalStatus.SUPERSEDED, at=at)
        return ids
