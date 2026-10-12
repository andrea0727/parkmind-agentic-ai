"""The MCP capability boundary stays a boundary (P0-24, P0-27; Architecture section 27 [C22, C23]).

* Delegation: ``tools/`` reaches the system only through use cases and the
  section 33 contracts, so no business logic can grow behind the transport
  (P0-24: "Server code delegates to application use-cases"; P0-27: "No planner
  logic is duplicated inside MCP handlers"). import-linter already forbids the
  core packages; this also keeps ports, clients and the graph out.
* No activation: neither the tools nor the use cases behind them can reach the
  proposal, persistence or activation paths (P0-24: "No MCP tool can activate or
  mutate the active plan").

AST-based, so a docstring that names a forbidden module is not an import.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "parkmind"
TOOLS = sorted((SRC / "tools").glob("*.py"))
BEHIND_THE_TOOLS = [
    SRC / "services" / "use_cases" / name
    for name in ("park_data_queries.py", "knowledge_queries.py", "planner_queries.py")
]

ALLOWED_PARKMIND_IMPORTS = (
    "parkmind.core.contracts",
    "parkmind.services.use_cases.",
    "parkmind.tools.",
)
FORBIDDEN_MODULES = (
    "parkmind.services.use_cases.propose_plan",
    "parkmind.services.use_cases.resolve_proposal",
    "parkmind.services.use_cases.persist_plan",
    "parkmind.graph",
)
FORBIDDEN_NAMES = {
    "ProposePlanUseCase",
    "ResolveProposalUseCase",
    "PersistPlanUseCase",
    "approve_plan",
    "activate",
    "supersede_pending",
}


def _imports(path: Path) -> list[str]:
    modules = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    found |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    found |= {
        alias.asname or alias.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        for alias in n.names
    }
    return found


def test_handlers_call_use_cases_only() -> None:
    offenders = {
        path.name: module
        for path in TOOLS
        for module in _imports(path)
        if module.startswith("parkmind")
        and not module.startswith(ALLOWED_PARKMIND_IMPORTS)
    }
    assert offenders == {}, (
        f"tools/ imports {offenders}: tools reach the system through use cases and "
        "contracts only, never ports, clients, planning or the graph."
    )


def test_no_tool_can_reach_proposal_persistence_or_activation() -> None:
    offenders = {}
    for path in [*TOOLS, *BEHIND_THE_TOOLS]:
        modules = [m for m in _imports(path) if m.startswith(FORBIDDEN_MODULES)]
        names = sorted(_names(path) & FORBIDDEN_NAMES)
        if modules or names:
            offenders[path.name] = modules + names
    assert offenders == {}, (
        f"{offenders}: MCP tools never propose, persist or activate a plan "
        "(section 27 [C23]); activation exists only on the approval path."
    )


def test_the_guard_sees_every_tool_module() -> None:
    names = {path.name for path in TOOLS}
    assert {
        "data_tools.py",
        "knowledge_tools.py",
        "planner_tools.py",
        "mcp_server.py",
    } <= names
    assert all(path.exists() for path in BEHIND_THE_TOOLS)
