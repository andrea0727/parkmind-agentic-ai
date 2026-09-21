"""PostgreSQL adapter for ``GuestRepository``."""

from parkmind.core.contracts import Guest
from parkmind.services.clients.postgres.codec import from_payload
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase


class PostgresGuestRepository(PostgresRepositoryBase):
    def save(self, guest: Guest) -> None:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO guests (guest_id, role, height_cm)
                VALUES (%s, %s, %s)
                ON CONFLICT (guest_id) DO UPDATE
                    SET role = EXCLUDED.role,
                        height_cm = EXCLUDED.height_cm,
                        updated_at = now()
                """,
                (guest.guest_id, guest.role.value, guest.height_cm),
            )

    def get(self, guest_id: str) -> Guest | None:
        with self._tx() as cur:
            cur.execute(
                "SELECT guest_id, role, height_cm FROM guests WHERE guest_id = %s",
                (guest_id,),
            )
            row = cur.fetchone()
        return None if row is None else from_payload(Guest, row, what="guest")

    def list_all(self) -> list[Guest]:
        with self._tx() as cur:
            cur.execute("SELECT guest_id, role, height_cm FROM guests ORDER BY guest_id")
            rows = cur.fetchall()
        return [from_payload(Guest, row, what="guest") for row in rows]
