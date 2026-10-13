"""P0-33 invariants, checked on the source (AST), not on behaviour.

* Only ``EventPolicy`` sets ``Event.severity`` / ``Event.requires_replan``
  (section 24: "Both feed the same deterministic EventPolicy, which alone
  assigns severity and requires_replan").
* The monitor, detector, policy and dedup never read the clock: ``now`` is
  always passed in (backlog P0-33 Done-when).
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "parkmind"
POLICY = SRC / "services" / "planning" / "event_policy.py"
POLICY_FIELDS = {"severity", "requires_replan"}
NO_CLOCK = [
    SRC / "services" / "planning" / "event_detector.py",
    SRC / "services" / "planning" / "event_policy.py",
    SRC / "services" / "planning" / "event_dedup.py",
    SRC / "services" / "use_cases" / "monitor_events.py",
    SRC / "services" / "use_cases" / "handle_events.py",
]
CLOCK_CALLS = {"now", "utcnow", "today", "time", "monotonic"}


def _sets_policy_fields(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            lines += [node.lineno for kw in node.keywords if kw.arg in POLICY_FIELDS]
        elif isinstance(node, ast.Dict):
            lines += [
                key.lineno
                for key in node.keys
                if isinstance(key, ast.Constant) and key.value in POLICY_FIELDS
            ]
        elif isinstance(node, ast.Assign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            lines += [
                t.lineno
                for t in targets
                if isinstance(t, ast.Attribute) and t.attr in POLICY_FIELDS
            ]
    return lines


def test_only_event_policy_sets_severity_or_requires_replan():
    offenders = [
        f"{path.relative_to(SRC)}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        if path != POLICY
        for line in _sets_policy_fields(ast.parse(path.read_text(encoding="utf-8")))
    ]

    assert offenders == []


def test_the_policy_does_set_them():
    """Guards the scan above: if this stops matching, the scan proves nothing."""
    assert _sets_policy_fields(ast.parse(POLICY.read_text(encoding="utf-8")))


def test_event_modules_never_read_the_clock():
    offenders = []
    for path in NO_CLOCK:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in CLOCK_CALLS
            ):
                offenders.append(f"{path.name}:{node.lineno} .{node.func.attr}()")

    assert offenders == []
