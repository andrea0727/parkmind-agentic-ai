"""Use case: persist a candidate plan for a thread.

Wraps the Postgres plan repository so agents/ never imports
services.clients directly (see .importlinter boundary contract).
"""

import psycopg

from parkmind.core.contracts import Plan
from parkmind.services.clients.postgres import PostgresPlanRepository, connect
from parkmind.services.ports.errors import RepositoryError


class PersistPlanUseCase:
    def execute(self, thread_id: str, plan: Plan) -> None:
        try:
            with connect() as conn:
                PostgresPlanRepository(conn).save(thread_id, plan)
        except (psycopg.Error, RepositoryError) as e:
            print(f"Warning: Could not save plan to database: {e}")
