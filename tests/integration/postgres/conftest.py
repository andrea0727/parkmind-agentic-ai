"""Fixtures for the real-PostgreSQL repository tests (P0-12).

Every test here talks to a throwaway database ``parkmind_test_<hex>`` created on
the server named by ``PARKMIND_TEST_DATABASE_URL`` (default: ``DATABASE_URL``),
migrated with Alembic and dropped afterwards -- the developer's own
``parkmind`` database is never touched.

When the server is unreachable the tests are skipped locally, but fail when the
``CI`` environment variable is set, so a missing Postgres service in CI cannot
turn the suite silently green.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from parkmind.config.settings import settings
from parkmind.services.clients.postgres import migrate

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_DB_PREFIX = "parkmind_test_"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark everything under tests/integration/postgres as `db`."""
    here = Path(__file__).parent
    for item in items:
        if item.path.is_relative_to(here):
            item.add_marker(pytest.mark.db)


def _url_for(base_url: str, database: str) -> str:
    return make_url(base_url).set(database=database).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def server_url() -> str:
    """Base URL of a reachable Postgres server; skips/fails when there is none."""
    explicit = os.getenv("PARKMIND_TEST_DATABASE_URL")
    url = explicit or settings.DATABASE_URL
    host = make_url(url).host or "localhost"
    if not explicit and host not in _LOCAL_HOSTS:
        pytest.skip(
            f"DATABASE_URL points at non-local host {host!r}; set "
            "PARKMIND_TEST_DATABASE_URL to run the db tests against it on purpose."
        )
    try:
        with psycopg.connect(_url_for(url, "postgres"), connect_timeout=3):
            pass
    except psycopg.OperationalError as exc:
        message = f"PostgreSQL not reachable ({exc}); run `docker compose up -d`."
        if os.getenv("CI"):
            pytest.fail(message)
        pytest.skip(message)
    return url


def _create_database(server: str) -> tuple[str, str]:
    name = f"{_DB_PREFIX}{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_url_for(server, "postgres"), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return name, _url_for(server, name)


def _drop_database(server: str, name: str) -> None:
    assert name.startswith(_DB_PREFIX), "refusing to drop a non-test database"
    with psycopg.connect(_url_for(server, "postgres"), autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
def empty_database_url(server_url: str) -> Iterator[str]:
    """A brand-new, un-migrated database (for migration tests)."""
    name, url = _create_database(server_url)
    try:
        yield url
    finally:
        _drop_database(server_url, name)


@pytest.fixture(scope="session")
def migrated_database_url(server_url: str) -> Iterator[str]:
    """One database migrated to head, shared by the repository tests."""
    name, url = _create_database(server_url)
    try:
        migrate.upgrade(url)
        yield url
    finally:
        _drop_database(server_url, name)


@pytest.fixture
def conn(migrated_database_url: str) -> Iterator[psycopg.Connection]:
    """A connection to the migrated database with every table emptied first."""
    connection = psycopg.connect(migrated_database_url, row_factory=dict_row)
    with connection.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
        )
        tables = [row["tablename"] for row in cur.fetchall()]
        if tables:
            cur.execute(
                sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(sql.Identifier(t) for t in tables)
                )
            )
    connection.commit()
    try:
        yield connection
    finally:
        if not connection.closed:  # a test may close it on purpose
            connection.rollback()
            connection.close()
