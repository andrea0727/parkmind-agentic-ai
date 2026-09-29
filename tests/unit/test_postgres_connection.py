"""Database-problem messages shared by the snapshot scripts (no database needed)."""

import psycopg
import pytest

from parkmind.services.clients.postgres.connection import (
    DATABASE_PROBLEMS,
    SCHEMA_HINT,
    describe_database_problem,
)
from parkmind.services.ports import RepositoryUnavailableError


@pytest.mark.parametrize(
    "exc",
    [psycopg.errors.UndefinedTable(), psycopg.errors.UndefinedColumn()],
)
def test_a_missing_schema_points_to_the_migration(exc: Exception) -> None:
    assert isinstance(exc, DATABASE_PROBLEMS)
    assert describe_database_problem(exc).endswith(SCHEMA_HINT)


@pytest.mark.parametrize(
    "exc",
    [
        psycopg.OperationalError("connection refused"),
        RepositoryUnavailableError("down"),
    ],
)
def test_an_unreachable_database_points_to_docker_compose(exc: Exception) -> None:
    assert isinstance(exc, DATABASE_PROBLEMS)
    assert "docker compose up -d" in describe_database_problem(exc)
