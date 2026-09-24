"""PostgreSQL adapter for ``ExecutionStateRepository`` (latest state per plan)."""

from parkmind.core.contracts import PlanExecutionState
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase


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

    def get(self, plan_id: str) -> PlanExecutionState | None:
        with self._tx() as cur:
            cur.execute("SELECT payload FROM execution_states WHERE plan_id = %s", (plan_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return from_payload(PlanExecutionState, row["payload"], what="execution state")
