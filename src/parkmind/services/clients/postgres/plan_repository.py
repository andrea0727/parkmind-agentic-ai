"""PostgreSQL adapter for ``PlanRepository``.

``plans`` rows are immutable bodies with no "active" flag. The active plan of a
thread is the row in ``active_plans``, and the only way in is ``activate``: one
``INSERT ... SELECT`` that succeeds only when an APPROVED proposal for that plan
exists in that thread, so the approval check and the switch cannot be separated
by a concurrent writer (section 23).
"""

from datetime import datetime

from parkmind.core.contracts import Plan
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase
from parkmind.services.clients.postgres.provenance_repository import (
    PostgresProvenanceRepository,
)
from parkmind.services.ports.errors import (
    NotApprovedError,
    NotFoundError,
    PlanImmutableError,
)


class PostgresPlanRepository(PostgresRepositoryBase):
    def save(self, thread_id: str, plan: Plan) -> None:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO plans (plan_id, version, thread_id, payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (plan_id) DO NOTHING
                RETURNING plan_id
                """,
                (plan.plan_id, plan.version, thread_id, to_jsonb(plan)),
            )
            if cur.fetchone() is None:
                cur.execute(
                    "SELECT thread_id, payload FROM plans WHERE plan_id = %s",
                    (plan.plan_id,),
                )
                stored = cur.fetchone()
                assert stored is not None  # the conflict proves the row exists
                same_body = from_payload(Plan, stored["payload"], what="plan") == plan
                if stored["thread_id"] != thread_id or not same_body:
                    raise PlanImmutableError("a different plan is already stored under this id")

            PostgresProvenanceRepository(self._conn).record("PLAN", plan.plan_id, plan.provenance)

    def get(self, plan_id: str) -> Plan | None:
        with self._tx() as cur:
            cur.execute("SELECT payload FROM plans WHERE plan_id = %s", (plan_id,))
            row = cur.fetchone()
        return None if row is None else from_payload(Plan, row["payload"], what="plan")

    def activate(self, thread_id: str, plan_id: str, *, at: datetime) -> None:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO active_plans (thread_id, plan_id, proposal_id, activated_at)
                SELECT p.thread_id, p.candidate_plan_id, p.proposal_id, %(at)s
                FROM proposals p
                WHERE p.thread_id = %(thread_id)s
                  AND p.candidate_plan_id = %(plan_id)s
                  AND p.approval_status = 'APPROVED'
                ORDER BY p.resolved_at DESC NULLS LAST, p.proposal_id
                LIMIT 1
                ON CONFLICT (thread_id) DO UPDATE
                    SET plan_id = EXCLUDED.plan_id,
                        proposal_id = EXCLUDED.proposal_id,
                        activated_at = EXCLUDED.activated_at
                RETURNING plan_id
                """,
                {"at": at, "thread_id": thread_id, "plan_id": plan_id},
            )
            if cur.fetchone() is not None:
                return

            cur.execute("SELECT 1 FROM plans WHERE plan_id = %s", (plan_id,))
            if cur.fetchone() is None:
                raise NotFoundError("plan does not exist")
            raise NotApprovedError("no APPROVED proposal for this plan in this thread")

    def get_active(self, thread_id: str) -> Plan | None:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT p.payload FROM active_plans a
                JOIN plans p ON p.plan_id = a.plan_id
                WHERE a.thread_id = %s
                """,
                (thread_id,),
            )
            row = cur.fetchone()
        return None if row is None else from_payload(Plan, row["payload"], what="plan")
