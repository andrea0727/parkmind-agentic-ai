"""Run the E2E scenarios against real ports into docs/e2e/raw/<id>.json.

Usage: python scripts/e2e/capture.py [scenario ...]    (FORCE=1 re-runs captured scenarios)
The scenarios live in tests/e2e/catalog; see tests/e2e/README.md.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO / "tests")]

from e2e.cli import main

if __name__ == "__main__":
    main(["capture", *sys.argv[1:]])
