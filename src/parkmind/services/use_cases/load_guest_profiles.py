"""Use case: load a guest's stored preference profile.

Wraps the Postgres profile repository so agents/ never imports
services.clients directly (see .importlinter boundary contract).
"""

import logging

import psycopg

from parkmind.core.contracts import GuestProfile
from parkmind.services.clients.postgres import PostgresProfileRepository, connect
from parkmind.services.ports.errors import RepositoryError

logger = logging.getLogger(__name__)


class LoadGuestProfilesUseCase:
    def execute(self, guest_id: str) -> list[GuestProfile]:
        try:
            with connect() as conn:
                profile = PostgresProfileRepository(conn).get_latest(guest_id)
                return [profile] if profile is not None else []
        except (psycopg.Error, RepositoryError) as e:
            logger.warning("Could not fetch profiles for %s: %s", guest_id, e)
            return []
