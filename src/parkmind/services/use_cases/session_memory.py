"""The one process-scoped ``SessionMemory`` every ``PostgresSessionStore`` shares.

``SessionMemory`` must outlive any single store (see
``services/clients/postgres/session_store.py``). Every store in the process is
built through ``session_store`` so ``session_only`` records written by the
intake survive until ``load_context`` reads them, whatever connection each uses.
"""

from typing import Any

import psycopg

from parkmind.services.clients.postgres import PostgresSessionStore, SessionMemory

SESSION_MEMORY = SessionMemory()


def session_store(conn: psycopg.Connection[Any]) -> PostgresSessionStore:
    """A store on ``conn`` backed by the process-wide ``SESSION_MEMORY``."""
    return PostgresSessionStore(conn, SESSION_MEMORY)
