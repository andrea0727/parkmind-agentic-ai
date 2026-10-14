"""Only ``validate_constraints`` may mark the party's constraints valid (§7 [C15]).

Every hard constraint the LLM extracts waits for a human confirmation interrupt, and
everything downstream checks ``constraints_valid`` (``ensure_confirmed``). That holds
only while one node -- the one that runs after the human confirmed -- can set it to
True. An AST walk over ``graph/`` and ``agents/`` finds every write of the key (a
dict-literal key, a subscript assignment, an ``.update(...)`` keyword) whose value is
not the literal ``False``.
"""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "parkmind"
PACKAGES = ("graph", "agents")
OWNER = ("elicitation_graph.py", "_validate_constraints")
KEY = "constraints_valid"


def _is_false(value: ast.expr | None) -> bool:
    return isinstance(value, ast.Constant) and value.value is False


def _writes(tree: ast.AST) -> list[tuple[int, ast.expr | None]]:
    """``(line, value)`` of every write of ``KEY`` in ``tree``."""
    found: list[tuple[int, ast.expr | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == KEY:
                    found.append((key.lineno, value))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == KEY
                ):
                    found.append((node.lineno, node.value))
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == KEY:
                    found.append((node.lineno, keyword.value))
    return found


def _top_level_functions(module: ast.Module) -> list[ast.FunctionDef]:
    return [node for node in module.body if isinstance(node, ast.FunctionDef)]


def test_only_validate_constraints_marks_the_constraints_valid():
    offenders: list[str] = []
    owner_sets_true = False
    for package in PACKAGES:
        for path in sorted((SOURCE / package).rglob("*.py")):
            module = ast.parse(path.read_text(encoding="utf-8"))
            for func in _top_level_functions(module):
                for line, value in _writes(func):
                    if (path.name, func.name) == OWNER:
                        owner_sets_true = owner_sets_true or not _is_false(value)
                    elif not _is_false(value):
                        offenders.append(
                            f"{path.relative_to(SOURCE)}:{line} ({func.name})"
                        )
            outside = [n for n in module.body if not isinstance(n, ast.FunctionDef)]
            for node in outside:
                for line, value in _writes(node):
                    if not _is_false(value):
                        offenders.append(
                            f"{path.relative_to(SOURCE)}:{line} (module scope)"
                        )

    assert owner_sets_true, "_validate_constraints no longer sets constraints_valid"
    assert not offenders, (
        "constraints_valid set to something other than False outside "
        "_validate_constraints:\n" + "\n".join(offenders)
    )


def test_the_walk_finds_each_way_of_writing_the_key():
    code = """
def node(state):
    state["constraints_valid"] = True
    state.update(constraints_valid=check())
    return {"constraints_valid": ok}
"""
    lines = [line for line, value in _writes(ast.parse(code)) if not _is_false(value)]

    assert sorted(lines) == [3, 4, 5]
