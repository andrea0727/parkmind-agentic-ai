"""Connection handling shared by the PostgreSQL repositories.

The caller owns the connection (``with connect(url) as conn:``); repositories
never open or close one, so several of them can share a single connection and
therefore a single transaction.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from parkmind.config.settings import settings
from parkmind.services.ports.errors import NotFoundError, RepositoryUnavailableError


def connect(url: str | None = None) -> psycopg.Connection[Any]:
    """Open a connection (``DATABASE_URL`` by default). The caller closes it."""
    return psycopg.connect(url or settings.DATABASE_URL)


class PostgresRepositoryBase:
    """Base of the repositories: one shared connection, one transaction helper."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    @contextmanager
    def _tx(self) -> Iterator[psycopg.Cursor[dict[str, Any]]]:
        """A dict-row cursor inside a transaction.

        ``conn.transaction()`` is the outermost transaction on an idle
        connection and a savepoint when the caller already opened one, so
        repository calls compose into the caller's unit of work.
        """
        try:
            with (
                self._conn.transaction(),
                self._conn.cursor(row_factory=dict_row) as cur,
            ):
                yield cur
        except psycopg.errors.ForeignKeyViolation as exc:
            raise NotFoundError(
                f"referenced row does not exist ({exc.diag.constraint_name})"
            ) from None
        except psycopg.OperationalError as exc:
            raise RepositoryUnavailableError("PostgreSQL is unavailable") from exc
