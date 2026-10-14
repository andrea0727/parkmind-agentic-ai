"""PostgreSQL adapter for ``ExecutionStateRepository`` (latest state per plan)."""

from parkmind.core.contracts import PlanExecutionState
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase
from parkmind.services.ports import NotFoundError


class PostgresExecutionStateRepository(PostgresRepositoryBase):
    def save(self, state: PlanExecutionState) -> bool:
        # The WHERE on DO UPDATE is the stale-write guard: an older `as_of`
        # matches nothing, returns no row, and leaves the stored state alone.
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO execution_states (plan_id, as_of, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (plan_id) DO UPDATE
                    SET as_of = EXCLUDED.as_of,
                        payload = EXCLUDED.payload,
                        updated_at = now()
                    WHERE execution_states.as_of <= EXCLUDED.as_of
                RETURNING plan_id
                """,
                (state.plan_id, state.as_of, to_jsonb(state)),
            )
            return cur.fetchone() is not None

    def lock(self, plan_id: str) -> None:
        # The plan row, not the state row: the state may not exist yet, and a row
        # that doesn't exist can't be locked. The lock lasts until the caller's
        # transaction ends (``_tx`` is a savepoint inside it).
        with self._tx() as cur:
            cur.execute(
                "SELECT plan_id FROM plans WHERE plan_id = %s FOR UPDATE", (plan_id,)
            )
            if cur.fetchone() is None:
                raise NotFoundError(f"plan {plan_id} does not exist")

    def get(self, plan_id: str) -> PlanExecutionState | None:
        with self._tx() as cur:
            cur.execute(
                "SELECT payload FROM execution_states WHERE plan_id = %s", (plan_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return from_payload(PlanExecutionState, row["payload"], what="execution state")
