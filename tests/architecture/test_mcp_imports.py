"""MCP is a transport (Architecture section 27 [C22]): the SDK may be imported
by the server adapter and by the client adapter the context loader can bind to
(P0-24), nowhere else. import-linter already keeps it out of the deterministic
core; this guard also keeps it out of use cases, the graph, agents and the rest
of tools/, so business logic can never grow behind the transport.

AST-based, so a docstring that mentions "import mcp" is not an import.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "parkmind"

ALLOWED = {
    "tools/mcp_server.py",
}
ALLOWED_PACKAGES = ("services/clients/mcp/",)


def _imports_mcp(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(
            alias.name == "mcp" or alias.name.startswith("mcp.") for alias in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and (node.module == "mcp" or node.module.startswith("mcp."))
        ):
            return True
    return False


def test_only_the_mcp_adapters_import_the_sdk() -> None:
    offenders = [
        rel
        for path in sorted(SRC.rglob("*.py"))
        if (rel := path.relative_to(SRC).as_posix()) not in ALLOWED
        and not rel.startswith(ALLOWED_PACKAGES)
        and _imports_mcp(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"{offenders} import the MCP SDK -- only tools/mcp_server.py (server) and "
        "services/clients/mcp/ (client adapter) may; the rest calls use cases."
    )


def test_the_server_adapter_does_import_the_sdk() -> None:
    """Keeps the guard honest: if the server moved, ALLOWED must follow it."""
    assert _imports_mcp((SRC / "tools" / "mcp_server.py").read_text(encoding="utf-8"))
