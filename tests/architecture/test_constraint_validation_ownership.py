"""Only ``validate_constraints`` may mark the party's constraints valid (§7 [C15]).

Every hard constraint the LLM extracts waits for a human confirmation interrupt, and
everything downstream checks ``constraints_valid`` (``ensure_confirmed``). That holds
only while one node -- the one that runs after the human confirmed -- can set it to
True. An AST walk over all of ``src/parkmind`` finds every write of the key (a
dict-literal key, a subscript assignment, an ``.update(...)`` keyword) whose value is
not the literal ``False``. The key is found written out, or through a module-level
constant that holds it (``KEY = "constraints_valid"``). A key built at run time
(``"constraints_" + "valid"``, a constant imported from another module) is not
followed: that would be deliberate evasion, which review catches, not this test.
"""

import ast
from collections.abc import Collection
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "parkmind"
OWNER = ("elicitation_graph.py", "_validate_constraints")
KEY = "constraints_valid"


def _is_false(value: ast.expr | None) -> bool:
    return isinstance(value, ast.Constant) and value.value is False


def _aliases(module: ast.Module) -> set[str]:
    """Module-level names bound to the key: ``KEY = "constraints_valid"``."""
    names: set[str] = set()
    for node in module.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if isinstance(value, ast.Constant) and value.value == KEY:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _is_key(node: ast.expr | None, aliases: Collection[str]) -> bool:
    if isinstance(node, ast.Constant):
        return node.value == KEY
    return isinstance(node, ast.Name) and node.id in aliases


def _writes(
    tree: ast.AST, aliases: Collection[str] = ()
) -> list[tuple[int, ast.expr | None]]:
    """``(line, value)`` of every write of ``KEY`` in ``tree``."""
    found: list[tuple[int, ast.expr | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if _is_key(key, aliases):
                    found.append((node.lineno if key is None else key.lineno, value))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _is_key(target.slice, aliases):
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
    for path in sorted(SOURCE.rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _aliases(module)
        for func in _top_level_functions(module):
            for line, value in _writes(func, aliases):
                if (path.name, func.name) == OWNER:
                    owner_sets_true = owner_sets_true or not _is_false(value)
                elif not _is_false(value):
                    offenders.append(f"{path.relative_to(SOURCE)}:{line} ({func.name})")
        outside = [n for n in module.body if not isinstance(n, ast.FunctionDef)]
        for node in outside:
            for line, value in _writes(node, aliases):
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


def test_the_walk_follows_a_module_constant_that_holds_the_key():
    code = """
VALID = "constraints_valid"

def node(state):
    state[VALID] = True
    return {VALID: ok}
"""
    module = ast.parse(code)
    lines = [
        line
        for line, value in _writes(module, _aliases(module))
        if not _is_false(value)
    ]

    assert sorted(lines) == [5, 6]
