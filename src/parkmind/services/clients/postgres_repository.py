"""
Postgres repository — persistence for guests, plans, proposals, behavior
signals and snapshots. Schema: database/init.sql.
"""


class PostgresRepository:
    def save_snapshot(self, snapshot: dict) -> str:
        raise NotImplementedError

    def get_latest_snapshot(self) -> dict | None:
        raise NotImplementedError
