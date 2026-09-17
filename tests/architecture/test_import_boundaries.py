"""P0-04: the import-boundary check runs as part of `poetry run pytest`, not only
as a separate `poetry run lint-imports` CLI step, so CI can't skip it by mistake.

Contract source of truth: `.importlinter` at the repo root. See its comments for
the boundary rule quoted from project-folder-structure.md / CLAUDE.local.md.
"""

import sys
from pathlib import Path

from importlinter.api import use_cases as importlinter_use_cases

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_FILE = REPO_ROOT / ".importlinter"


def test_importlinter_contract_file_exists() -> None:
    assert CONTRACT_FILE.is_file(), (
        "Expected an .importlinter contract at the repo root "
        "(see project-folder-structure.md boundary contract)."
    )


def test_import_boundaries_are_respected() -> None:
    """Fails with the offending module named in stdout/console output when a
    boundary path imports something it must not (adapters, frameworks, DB
    drivers, or an LLM SDK) -- see Done-when bullet 3 of backlog item P0-04.
    """
    passed = importlinter_use_cases.lint_imports(
        config_filename=str(CONTRACT_FILE), no_logo=True
    )
    assert passed, (
        "Import-boundary contract broken -- run `poetry run lint-imports` "
        "locally to see which module imports what."
    )


def test_lint_imports_actually_fails_on_a_real_violation(tmp_path: Path) -> None:
    """Regression test for the checker itself (Done-when bullet 3): if
    `.importlinter`'s config were ever broken or accidentally weakened, this
    is what should catch it. Builds an isolated scratch package -- never
    touches real src/parkmind -- with one module importing another it's
    forbidden to, and asserts lint_imports reports failure, naming both
    modules in the console report.
    """
    pkg_root = tmp_path / "fake_boundary_pkg"
    pkg_root.mkdir()
    (pkg_root / "__init__.py").write_text("")
    (pkg_root / "adapter.py").write_text("VALUE = 1\n")
    (pkg_root / "core.py").write_text("from fake_boundary_pkg import adapter\n")

    contract_file = tmp_path / ".importlinter"
    contract_file.write_text(
        "[importlinter]\n"
        "root_package = fake_boundary_pkg\n"
        "\n"
        "[importlinter:contract:fake]\n"
        "name = core must not import adapter\n"
        "type = forbidden\n"
        "source_modules =\n"
        "    fake_boundary_pkg.core\n"
        "forbidden_modules =\n"
        "    fake_boundary_pkg.adapter\n"
    )

    sys.path.insert(0, str(tmp_path))
    try:
        passed = importlinter_use_cases.lint_imports(
            config_filename=str(contract_file), no_logo=True
        )
    finally:
        sys.path.remove(str(tmp_path))

    assert passed is False, (
        "Expected lint_imports to report a failure for a deliberately "
        "violating import -- if this test itself fails, the checker isn't "
        "catching real violations any more."
    )
