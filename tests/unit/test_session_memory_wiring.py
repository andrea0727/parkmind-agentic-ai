"""Every PostgresSessionStore in the process shares the one SESSION_MEMORY (P0-30).

``session_only`` requirements written by the intake must still be there when
``load_context`` reads them, whatever connection each store uses.
"""

import ast
from pathlib import Path
from unittest.mock import MagicMock

from parkmind.services.use_cases import accessibility_intake, planning_deps
from parkmind.services.use_cases.session_memory import SESSION_MEMORY, session_store

SRC = Path(__file__).parents[2] / "src" / "parkmind"


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def close(self) -> None:
        pass


def test_session_store_uses_the_process_wide_memory():
    assert session_store(_Conn())._memory is SESSION_MEMORY  # type: ignore[arg-type]
    assert session_store(_Conn())._memory is session_store(_Conn())._memory  # type: ignore[arg-type]


def test_intake_and_planning_deps_build_their_stores_on_the_same_memory(monkeypatch):
    monkeypatch.setattr(accessibility_intake, "connect", _Conn)
    monkeypatch.setattr(planning_deps, "connect", _Conn)
    for name in (
        "ThemeParksClient",
        "OpenMeteoClient",
        "PostgresAttractionRepository",
        "PostgresIdMappingRepository",
        "PostgresSnapshotRepository",
        "magic_kingdom_knowledge_store",
        "RoutingClient",
        "SnapshotCollector",
    ):
        monkeypatch.setattr(planning_deps, name, MagicMock())

    with accessibility_intake._default_store() as intake_store:
        pass
    with planning_deps.default_planning_deps() as deps:
        planning_store = deps.sessions

    assert intake_store._memory is SESSION_MEMORY  # type: ignore[attr-defined]
    assert planning_store._memory is SESSION_MEMORY  # type: ignore[attr-defined]


def test_no_service_code_builds_a_session_store_with_its_own_memory():
    """Only the helper (and the dev seed, which writes a persisted record) construct one."""
    allowed = {"use_cases/session_memory.py", "clients/postgres/seed.py", "clients/postgres/session_store.py"}
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC / "services").as_posix() if "services" in path.parts else path.as_posix()
        if rel in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "PostgresSessionStore":
                offenders.append(rel)
    assert offenders == []
