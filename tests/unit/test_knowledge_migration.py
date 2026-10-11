"""Migration 0003 on a server without pgvector: an actionable refusal (P0-26).

CI's and compose's Postgres always have pgvector, so the guard is exercised here
against a stub connection that reports no ``vector`` extension available.
"""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from alembic.util import CommandError

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "database"
    / "migrations"
    / "versions"
    / "0003_knowledge_pgvector.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0003", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Bind:
    def __init__(self, available: bool) -> None:
        self.available = available

    def execute(self, statement: Any) -> Any:
        return SimpleNamespace(scalar=lambda: 1 if self.available else None)


def _op(available: bool, executed: list[str]) -> Any:
    return SimpleNamespace(
        get_bind=lambda: _Bind(available), execute=lambda sql: executed.append(sql)
    )


def test_upgrade_without_pgvector_stops_with_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration()
    executed: list[str] = []
    monkeypatch.setattr(migration, "op", _op(False, executed))

    with pytest.raises(CommandError, match="docker compose up -d --build"):
        migration.upgrade()

    assert executed == []  # nothing ran before the refusal


def test_upgrade_with_pgvector_creates_the_extension_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration()
    executed: list[str] = []
    monkeypatch.setattr(migration, "op", _op(True, executed))

    migration.upgrade()

    assert executed[0] == "CREATE EXTENSION IF NOT EXISTS vector"
