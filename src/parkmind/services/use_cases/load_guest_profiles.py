"""Use case: load a guest's stored preference profile.

Wraps the Postgres profile repository so agents/ never imports
services.clients directly (see .importlinter boundary contract).
"""

import psycopg

from parkmind.core.contracts import GuestProfile
from parkmind.services.clients.postgres import PostgresProfileRepository, connect
from parkmind.services.ports.errors import RepositoryError


class LoadGuestProfilesUseCase:
    def execute(self, guest_id: str) -> list[GuestProfile]:
        try:
            with connect() as conn:
                profile = PostgresProfileRepository(conn).get_latest(guest_id)
                return [profile] if profile is not None else []
        except (psycopg.Error, RepositoryError) as e:
            print(f"Warning: Could not fetch profiles for {guest_id}: {e}")
            return []
