"""PostgreSQL adapter for ``ProposalRepository``.

The mutable fields (``approval_status``, ``rejection_reason``) live in columns,
which are the source of truth; ``payload`` is the body as first saved. Reading
overlays the columns on the payload.
"""

from datetime import datetime
from typing import Any

import psycopg

from parkmind.core.contracts import ApprovalStatus, Proposal, RejectionReason
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase
from parkmind.services.clients.postgres.provenance_repository import (
    PostgresProvenanceRepository,
)
from parkmind.services.ports.errors import (
    InvalidStateTransitionError,
    NotFoundError,
    PendingProposalExistsError,
    ProposalImmutableError,
)

_ONE_PENDING_INDEX = "proposals_one_pending_per_thread"


def _overlay(payload: Any, status: str, rejection_reason: str | None) -> Proposal:
    body = {**payload, "approval_status": status, "rejection_reason": rejection_reason}
    return from_payload(Proposal, body, what="proposal")


class PostgresProposalRepository(PostgresRepositoryBase):
    def save(self, thread_id: str, proposal: Proposal) -> None:
        with self._tx() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO proposals
                        (proposal_id, thread_id, base_plan_id, candidate_plan_id,
                         triggering_event_id, approval_status, rejection_reason, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (proposal_id) DO NOTHING
                    RETURNING proposal_id
                    """,
                    (
                        proposal.proposal_id,
                        thread_id,
                        proposal.base_plan_id,
                        proposal.candidate_plan_id,
                        proposal.triggering_event_id,
                        proposal.approval_status.value,
                        proposal.rejection_reason.value if proposal.rejection_reason else None,
                        to_jsonb(proposal),
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                if exc.diag.constraint_name == _ONE_PENDING_INDEX:
                    raise PendingProposalExistsError(
                        "the thread already holds a PENDING proposal"
                    ) from None
                raise

            if cur.fetchone() is None:
                self._require_same_body(cur, thread_id, proposal)

            PostgresProvenanceRepository(self._conn).record(
                "PROPOSAL", proposal.proposal_id, proposal.provenance
            )

    def get(self, proposal_id: str) -> Proposal | None:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT payload, approval_status, rejection_reason
                FROM proposals WHERE proposal_id = %s
                """,
                (proposal_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return _overlay(row["payload"], row["approval_status"], row["rejection_reason"])

    def list_pending(self, thread_id: str) -> list[Proposal]:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT payload, approval_status, rejection_reason FROM proposals
                WHERE thread_id = %s AND approval_status = 'PENDING'
                ORDER BY created_at, proposal_id
                """,
                (thread_id,),
            )
            rows = cur.fetchall()
        return [_overlay(r["payload"], r["approval_status"], r["rejection_reason"]) for r in rows]

    def resolve(
        self,
        proposal_id: str,
        status: ApprovalStatus,
        *,
        at: datetime,
        rejection_reason: RejectionReason | None = None,
    ) -> Proposal:
        if status is ApprovalStatus.PENDING:
            raise InvalidStateTransitionError("a proposal cannot be resolved back to PENDING")

        with self._tx() as cur:
            cur.execute(
                """
                UPDATE proposals
                SET approval_status = %s, rejection_reason = %s, resolved_at = %s
                WHERE proposal_id = %s AND approval_status = 'PENDING'
                RETURNING payload, approval_status, rejection_reason
                """,
                (
                    status.value,
                    rejection_reason.value if rejection_reason else None,
                    at,
                    proposal_id,
                ),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT 1 FROM proposals WHERE proposal_id = %s", (proposal_id,))
                if cur.fetchone() is None:
                    raise NotFoundError("proposal does not exist")
                raise InvalidStateTransitionError("only a PENDING proposal can be resolved")
        return _overlay(row["payload"], row["approval_status"], row["rejection_reason"])

    def supersede_pending(self, thread_id: str, *, at: datetime) -> list[str]:
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE proposals
                SET approval_status = 'SUPERSEDED', resolved_at = %s
                WHERE thread_id = %s AND approval_status = 'PENDING'
                RETURNING proposal_id
                """,
                (at, thread_id),
            )
            return sorted(row["proposal_id"] for row in cur.fetchall())

    @staticmethod
    def _require_same_body(cur: psycopg.Cursor[dict[str, Any]], thread_id: str, proposal: Proposal) -> None:
        """An existing proposal_id may only be re-saved with the same body.

        The incoming resolution is overlaid on the stored body first, so a stale
        retry of ``save(PENDING)`` after approval is a harmless no-op that leaves
        the resolution alone, while any other difference is refused.
        """
        cur.execute(
            "SELECT thread_id, payload FROM proposals WHERE proposal_id = %s",
            (proposal.proposal_id,),
        )
        stored = cur.fetchone()
        assert stored is not None  # the conflict proves the row exists
        rebuilt = _overlay(
            stored["payload"],
            proposal.approval_status.value,
            proposal.rejection_reason.value if proposal.rejection_reason else None,
        )
        if stored["thread_id"] != thread_id or rebuilt != proposal:
            raise ProposalImmutableError("a different proposal is already stored under this id")
