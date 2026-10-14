"""Judge the captures in docs/e2e/raw and render REPORT.md plus the Allure results (no credentials needed)."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO / "tests")]

from e2e.cli import main

if __name__ == "__main__":
    main(["report"])
