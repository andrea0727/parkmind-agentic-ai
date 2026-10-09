"""The one process-scoped ``SessionMemory`` every ``PostgresSessionStore`` shares.

``SessionMemory`` must outlive any single store (see
``services/clients/postgres/session_store.py``); use cases that open a store
per call pass this instance so ``session_only`` records survive across calls.
"""

from parkmind.services.clients.postgres import SessionMemory

SESSION_MEMORY = SessionMemory()
