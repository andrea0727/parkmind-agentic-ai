"""PostgreSQL adapter for ``AttractionRepository``."""

from collections.abc import Sequence
from datetime import date

from parkmind.core.contracts import PARK_TZ, Attraction, Park
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase


class PostgresAttractionRepository(PostgresRepositoryBase):
    def save_catalog(self, park_id: str, attractions: Sequence[Attraction]) -> None:
        with self._tx() as cur:
            cur.executemany(
                """
                INSERT INTO attractions (node_id, park_id, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (node_id) DO UPDATE
                    SET park_id = EXCLUDED.park_id,
                        payload = EXCLUDED.payload,
                        updated_at = now()
                """,
                [(a.node_id, park_id, to_jsonb(a)) for a in attractions],
            )

    def list_attractions(self, park_id: str) -> list[Attraction]:
        with self._tx() as cur:
            cur.execute(
                "SELECT payload FROM attractions WHERE park_id = %s ORDER BY node_id",
                (park_id,),
            )
            rows = cur.fetchall()
        return [from_payload(Attraction, r["payload"], what="attraction") for r in rows]

    def get_attraction(self, node_id: str) -> Attraction | None:
        with self._tx() as cur:
            cur.execute("SELECT payload FROM attractions WHERE node_id = %s", (node_id,))
            row = cur.fetchone()
        return None if row is None else from_payload(Attraction, row["payload"], what="attraction")

    def save_schedule(self, park: Park) -> None:
        service_date = park.opening_time.astimezone(PARK_TZ).date()
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO park_schedules (park_id, service_date, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (park_id, service_date) DO UPDATE
                    SET payload = EXCLUDED.payload, updated_at = now()
                """,
                (park.park_id, service_date, to_jsonb(park)),
            )

    def get_schedule(self, park_id: str, on_date: date) -> Park | None:
        with self._tx() as cur:
            cur.execute(
                "SELECT payload FROM park_schedules WHERE park_id = %s AND service_date = %s",
                (park_id, on_date),
            )
            row = cur.fetchone()
        return None if row is None else from_payload(Park, row["payload"], what="park schedule")
