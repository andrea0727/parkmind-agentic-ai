"""PostgreSQL adapter for ``ProfileRepository`` (append-only version history)."""

from parkmind.core.contracts import GuestProfile
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase
from parkmind.services.ports.errors import NotFoundError, ProfileVersionConflictError


class PostgresProfileRepository(PostgresRepositoryBase):
    def save(self, profile: GuestProfile) -> None:
        with self._tx() as cur:
            # NO KEY UPDATE serializes concurrent writers of the same guest's
            # profile without blocking the FK checks of other child inserts.
            cur.execute(
                "SELECT 1 FROM guests WHERE guest_id = %s FOR NO KEY UPDATE",
                (profile.guest_id,),
            )
            if cur.fetchone() is None:
                raise NotFoundError("guest does not exist")

            cur.execute(
                """
                SELECT profile_version, payload FROM guest_profiles
                WHERE guest_id = %s ORDER BY profile_version DESC LIMIT 1
                """,
                (profile.guest_id,),
            )
            latest = cur.fetchone()
            if latest is not None:
                if latest["profile_version"] == profile.profile_version:
                    stored = from_payload(GuestProfile, latest["payload"], what="profile")
                    if stored == profile:
                        return  # identical retry: idempotent
                if profile.profile_version <= latest["profile_version"]:
                    raise ProfileVersionConflictError(
                        "profile_version must be greater than the latest stored version"
                    )

            cur.execute(
                """
                INSERT INTO guest_profiles (guest_id, profile_version, payload)
                VALUES (%s, %s, %s)
                """,
                (profile.guest_id, profile.profile_version, to_jsonb(profile)),
            )

    def get_latest(self, guest_id: str) -> GuestProfile | None:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT payload FROM guest_profiles
                WHERE guest_id = %s ORDER BY profile_version DESC LIMIT 1
                """,
                (guest_id,),
            )
            row = cur.fetchone()
        return None if row is None else from_payload(GuestProfile, row["payload"], what="profile")

    def get_version(self, guest_id: str, profile_version: int) -> GuestProfile | None:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT payload FROM guest_profiles
                WHERE guest_id = %s AND profile_version = %s
                """,
                (guest_id, profile_version),
            )
            row = cur.fetchone()
        return None if row is None else from_payload(GuestProfile, row["payload"], what="profile")
