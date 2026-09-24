"""Guard against issue #56 recurring: services/clients/_retry.py is the one
place a retry loop should sleep. Before the extraction, ThemeParksClient and
OpenMeteoClient each carried their own copy of the same loop -- including,
before self-review, the same two bugs. This fails the build the moment a new
adapter (P0-09 routing, P1-06 Queue-Times, ...) writes `time.sleep(...)`
instead of calling `services.clients._retry.send_with_retry`.

AST-based, not a text/regex scan, so a comment or docstring mentioning
"time.sleep(" can't produce a false positive -- and, deliberately, no
`_retry.py` is excluded here: it never calls `time.sleep(...)` as a call
expression either. It only references the function object once (`sleep if
sleep is not None else time.sleep`) and calls the resolved `_sleep(...)`
local instead, precisely so a test can monkeypatch `_retry.time.sleep` for
every caller, including ones that never pass `sleep=` explicitly.
"""

import ast
from pathlib import Path

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "src" / "parkmind" / "services" / "clients"


def _calls_time_sleep(source: str) -> bool:
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "sleep"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "time"
        for node in ast.walk(tree)
    )


def test_no_client_calls_time_sleep_directly() -> None:
    offenders = [
        path.name
        for path in sorted(CLIENTS_DIR.glob("*.py"))
        if _calls_time_sleep(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"{offenders} call time.sleep(...) directly -- a retry loop belongs "
        "in services.clients._retry.send_with_retry, not copied into a new "
        "adapter (issue #56)."
    )
