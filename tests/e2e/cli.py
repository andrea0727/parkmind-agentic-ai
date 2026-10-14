"""Command line of the scenario catalog: list, capture on real ports, build the report, write the matrix."""

import argparse
import json
import os
import sys
from collections.abc import Sequence

from e2e import catalog
from e2e.kit.driver import run_scenario
from e2e.kit.env import RAW_DIR, REPO
from e2e.kit.live import LiveRuntime
from e2e.kit.matrix import render_matrix
from e2e.kit.report import build_report

MATRIX_PATH = REPO / "docs" / "testing" / "SCENARIO_MATRIX.md"


def capture(ids: Sequence[str]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    runtime = LiveRuntime()
    for scenario in catalog.ordered(list(ids)):
        if not scenario.live:
            continue
        target = RAW_DIR / f"{scenario.id}.json"
        if target.exists() and not os.environ.get("FORCE"):
            print(f"skip {scenario.id} (already captured)", flush=True)
            continue
        out = run_scenario(scenario, runtime)
        target.write_text(json.dumps(out, indent=2, default=str))
        exc = out.get("exception", {}).get("type")
        print(f"done {scenario.id} in {out['seconds']}s exc={exc}", flush=True)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="e2e", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="print the catalog")
    run = sub.add_parser(
        "capture", help="run scenarios on real ports into docs/e2e/raw"
    )
    run.add_argument("ids", nargs="*")
    sub.add_parser(
        "report", help="judge the captures and build REPORT.md and the Allure results"
    )
    sub.add_parser("matrix", help="write docs/testing/SCENARIO_MATRIX.md")
    args = parser.parse_args(argv)
    match args.command:
        case "list":
            for s in catalog.SCENARIOS:
                print(f"{s.number:>3} {s.id:<40} {','.join(s.layers())}")
        case "capture":
            capture(args.ids)
        case "report":
            build_report(catalog.SCENARIOS)
        case "matrix":
            MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
            MATRIX_PATH.write_text(render_matrix(catalog.SCENARIOS))
            print(f"wrote {MATRIX_PATH.relative_to(REPO)}")


if __name__ == "__main__":
    main(sys.argv[1:])
