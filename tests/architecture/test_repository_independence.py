"""P0-12 Done-when: "Repositories are testable independently of the HTTP layer."

Importing the repository ports and the Postgres adapters must not drag in the
HTTP layer (`fastapi`, `parkmind.api`) or any LLM/graph framework -- so a
repository can be built from a bare connection in any test or script.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = textwrap.dedent(
    """
    import sys

    import parkmind.services.clients.postgres  # noqa: F401
    import parkmind.services.ports  # noqa: F401

    forbidden = ("fastapi", "starlette", "uvicorn", "langgraph", "mcp",
                 "streamlit", "anthropic", "langchain_anthropic")
    leaked = sorted(
        name for name in sys.modules
        if name == "parkmind.api" or name.startswith("parkmind.api.")
        or name.split(".")[0] in forbidden
    )
    print("LEAKED:" + ",".join(leaked))
    """
)


def test_ports_and_postgres_adapters_do_not_import_http_layer() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "LEAKED:", result.stdout
