"""Builds docs/e2e/REPORT.md and Allure results (BDD steps) from the captured facts.

Needs no credentials: it only reads the captured JSON. Every run is kept under
build/e2e/runs/<timestamp>_<passed>of<total>/ (gitignored), the previous run's Allure history is
carried forward so the trend graph grows, and build/e2e/latest points at the newest run.
Rendering the interactive report needs the Allure CLI.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from e2e.kit.env import RAW_DIR, REPO, REPORT_DIR, RUNS_DIR
from e2e.model import Facts, Scenario

Attach = Callable[[str, str, str], dict[str, str]]

KNOWN_ISSUE_B2 = (
    "Known issue B2: the plan starts at {fa} but the park time is {now}; "
    "every plan starts at park.opening_time"
)
CATEGORIES = (
    ("B1", "Height never asked"),
    ("B2", "Plan starts in the past"),
    ("B5", "Explanation omits who skips"),
    ("B6", "Malformed payload crashes"),
    ("B7", "Outages surface as raw exceptions"),
    ("B8", "No adult-supervision rule"),
    ("B10", "LLM alters safety-critical values"),
    ("B11", "Optimizer and checker disagree on opening hours"),
)
MARK = {"passed": "[x]", "failed": "[ ] FAILED:", "broken": "[!]", "skipped": "[-]"}


def md_status(status: str) -> str:
    return {
        "passed": "PASSED",
        "failed": "FAILED",
        "broken": "BROKEN",
        "skipped": "SKIPPED",
    }[status]


def build_steps(
    scenario: Scenario, r: Facts, status: str, msg: str, attach: Attach
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    f2 = r.get("facts2", {})

    def step(
        name: str, st: str = "passed", atts: list[dict[str, str]] | None = None
    ) -> None:
        steps.append({"name": name, "status": st, "atts": atts or []})

    step(f"Setup: {scenario.setup}")
    step(
        f"Guests send {len(r['messages'])} message(s)",
        atts=[attach("Guest messages", "\n\n".join(r["messages"]), "text/plain")],
    )
    ex = r.get("extracted")
    if ex:
        step(
            f"The app understood: party of {ex['party_size']}, must-do {ex['must_do']}, "
            f"{len(ex['avoid'] or [])} ride(s) to avoid, departure {ex['departure_time']}, "
            f"lunch {ex['lunch_window']}, heights {ex['heights']}",
            atts=[
                attach("LLM extraction", json.dumps(ex, indent=2), "application/json")
            ],
        )
    i1 = r.get("interrupt1")
    if i1:
        step(
            f"The app stops and asks the guests ({i1['kind']})",
            atts=[
                attach(
                    "Interrupt payload",
                    json.dumps(i1["payload"], indent=2),
                    "application/json",
                )
            ],
        )
    elif ex:
        step("The app does not need to ask anything else")
    if i1 and i1["kind"] == "hard_constraint_confirmation":
        step(f"Guests answer the confirmation with {json.dumps(r['confirm_payload'])}")
        stages = r.get("stages", [])
        step(f"Planning stages that ran: {' -> '.join(stages) if stages else 'none'}")
        if f2.get("violations") is not None:
            v = f2["violations"]
            valid = "valid" if f2.get("check_valid") else "NOT valid"
            rules = f" with {', '.join(x['rule'] for x in v)}" if v else ""
            step(
                f"The rule checker says the plan is {valid}{rules}",
                atts=[attach("Violations", json.dumps(v, indent=2), "application/json")]
                if v
                else [],
            )
        if f2.get("n_stops"):
            step(
                f"The plan has {f2['n_stops']} stops ({f2['first_arrival']} to {f2['last_arrival']})",
                atts=[attach("Explanation", f2["explanation"], "text/plain")]
                if f2.get("explanation")
                else [],
            )
            if f2["first_arrival"] < r["now"]:
                step(
                    KNOWN_ISSUE_B2.format(fa=f2["first_arrival"], now=r["now"]),
                    "broken",
                )
        i2 = r.get("interrupt2")
        if i2 and i2["kind"] != "plan_approval":
            step(f"The app stops again ({i2['kind']}): {f2.get('last_ai', '')[:200]}")
        elif not i2 and f2.get("last_ai"):
            step(f"The app tells the guests: {f2['last_ai'][:240]}")
        if i2 and i2["kind"] == "plan_approval":
            step("The app asks the guests to approve the plan (it is not active yet)")
            step(f"Guests decide {json.dumps(r['approval_payload'])}")
            step(
                f"After the decision: stages {r.get('stages_after_approval')}, "
                f"approval {r.get('approval')}, plan active: {r.get('plan_active')}"
            )
    replan = r.get("replan")
    if replan and replan.get("sent"):
        step(
            f"The monitor reports {replan['closed']} closed",
            atts=[
                attach(
                    "Replanning facts", json.dumps(replan, indent=2), "application/json"
                )
            ],
        )
        step(
            "The replanning graph proposes an alternative"
            if replan.get("proposed")
            else "The replanning graph proposed nothing"
        )
    if e := r.get("exception"):
        step(
            f"The run crashed with {e['type']}: {e['message'][:240]}",
            "failed" if status == "failed" else "broken",
            [attach("Exception", f"{e['type']}: {e['message']}", "text/plain")],
        )
    why = f"\n\nWhy: {scenario.why}" if scenario.why and status != "passed" else ""
    step(
        f"Expected: {scenario.expected[0].lower() + scenario.expected[1:]}",
        status,
        [attach("Verdict", msg + why, "text/plain")],
    )
    return steps


def to_allure(steps: list[dict[str, Any]], t0: int, t1: int) -> list[dict[str, Any]]:
    return [
        {
            "name": s["name"],
            "status": s["status"],
            "stage": "finished",
            "start": t0,
            "stop": t1,
            "steps": [],
            "parameters": [],
            "attachments": s["atts"],
        }
        for s in steps
    ]


def description(scenario: Scenario, r: Facts) -> str:
    messages = "\n".join(
        f"> {line}" if line else ">"
        for m in r["messages"]
        for line in (m + "\n").splitlines()
    )
    criteria = scenario.criteria
    layers = [
        f"- **{name}:** {text}"
        for name, text in (
            ("Workflow", criteria.workflow),
            ("LLM evaluation", criteria.llm_eval),
            ("Observability", criteria.observability),
            ("Business rules", criteria.business_rules),
        )
        if text
    ]
    return "\n".join(
        [
            f"## Scenario {scenario.number}: {scenario.title}",
            "",
            f"**What is being tested:** {scenario.what}",
            "",
            f"**Area:** {scenario.area} | **Severity:** {scenario.severity}",
            "",
            "### Steps the test performs",
            *[f"{i}. {t}" for i, t in enumerate(scenario.steps, 1)],
            "",
            "### Expected result",
            scenario.expected,
            "",
            "### Criteria at the other layers",
            *(layers or ["- none"]),
            "",
            "### Setup / injected fault",
            scenario.setup,
            "",
            "### Exact guest message(s) sent",
            messages,
        ]
    )


def run_dir_name(raw: Path) -> str:
    """Name a run after the newest capture, so rebuilding the same capture reuses its folder."""
    newest = max(f.stat().st_mtime for f in raw.glob("*.json"))
    return datetime.fromtimestamp(newest).astimezone().strftime("%Y-%m-%d_%H%M%S")


def previous_report(current: str) -> Path | None:
    """The newest earlier run that has a rendered Allure report (its history/ feeds the trend)."""
    older = sorted(
        d
        for d in RUNS_DIR.glob("*/allure-report/history")
        if d.parents[1].name[:17] < current
    )
    return older[-1] if older else None


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    )
    return out.stdout.strip()


def build_report(scenarios: Sequence[Scenario], raw: Path = RAW_DIR) -> dict[str, Any]:
    if not list(raw.glob("*.json")):
        sys.exit(f"No captures in {raw}; run the capture first")
    run_name = run_dir_name(raw)
    prev = previous_report(run_name)
    run = RUNS_DIR / run_name
    results = run / "allure-results"
    for stale in RUNS_DIR.glob(f"{run_name}*"):
        shutil.rmtree(stale)
    results.mkdir(parents=True)
    shutil.copytree(raw, run / "raw")
    if prev:
        shutil.copytree(prev, results / "history")
    rows: list[tuple[int, str, str, str, str]] = []
    md: list[str] = []
    for scenario in sorted(scenarios, key=lambda s: s.number):
        path = raw / f"{scenario.id}.json"
        if not path.exists():
            print("missing", scenario.id)
            continue
        r = json.loads(path.read_text())
        status, msg = scenario.judge(r)

        def attach(name: str, content: str, mime: str) -> dict[str, str]:
            ext = "json" if mime == "application/json" else "txt"
            source = f"{uuid.uuid4()}-attachment.{ext}"
            (results / source).write_text(content)
            return {"name": name, "source": source, "type": mime}

        steps = build_steps(scenario, r, status, msg, attach)
        t1 = int(path.stat().st_mtime * 1000)
        t0 = t1 - int(r.get("seconds", 0) * 1000)
        full = f"e2e.real_ports.{scenario.id}"
        digest = hashlib.md5(full.encode()).hexdigest()
        why = f"\nWhy: {scenario.why}" if scenario.why and status != "passed" else ""
        result = {
            "uuid": str(uuid.uuid4()),
            "historyId": digest,
            "testCaseId": digest,
            "fullName": full,
            "name": f"{scenario.number:02d} - {scenario.title}",
            "status": status,
            "stage": "finished",
            "start": t0,
            "stop": t1,
            "statusDetails": {"message": msg + why, "trace": ""},
            "description": description(scenario, r),
            "labels": [
                {"name": "epic", "value": "Initial planning graph"},
                {"name": "feature", "value": scenario.area},
                {"name": "severity", "value": scenario.severity},
                {"name": "suite", "value": "E2E on real ports"},
                {"name": "framework", "value": "scenario catalog"},
                *({"name": "tag", "value": layer} for layer in scenario.layers()),
            ],
            "steps": to_allure(steps, t0, t1),
            "attachments": [],
            "parameters": [],
        }
        (results / f"{result['uuid']}-result.json").write_text(
            json.dumps(result, indent=1)
        )
        rows.append((scenario.number, scenario.title, status, scenario.area, msg))
        md += [
            f"### {scenario.number:02d} - {scenario.title}  `{md_status(status)}`",
            "",
            (
                f"*Feature:* {scenario.area} | *Severity:* {scenario.severity} | "
                f"*Layers:* {', '.join(scenario.layers())} | *Duration:* {r.get('seconds')} s"
            ),
            f"*What is tested:* {scenario.what}",
            "",
        ]
        md += [f"- {MARK[s['status']]} {s['name']}" for s in steps]
        md += ["", f"**Verdict:** {msg}"]
        if scenario.why and status != "passed":
            md.append(f"**Why:** {scenario.why}")
        md.append("")

    counts: dict[str, int] = {}
    for _, _, status, _, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    head = [
        "# E2E report on real ports - initial planning graph",
        "",
        (
            "Generated from live runs: Postgres, ThemeParks, Open-Meteo, the real clock and the Anthropic LLM. "
            "Raw per-scenario data: `docs/e2e/raw/`. How to reproduce: `tests/e2e/README.md`. "
            "Interactive version: render the Allure results with `allure generate`."
        ),
        "",
        (
            f"**Total {len(rows)}** | passed {counts.get('passed', 0)} | "
            f"failed {counts.get('failed', 0)} | broken {counts.get('broken', 0)}"
        ),
        "",
        "| # | Scenario | Status | Feature |",
        "|---|----------|--------|---------|",
        *[f"| {n} | {t} | {md_status(s)} | {fe} |" for n, t, s, fe, _ in rows],
        "",
        "---",
        "",
    ]
    (REPORT_DIR / "REPORT.md").write_text("\n".join(head + md))

    categories = [
        {
            "name": f"{b} {n}",
            "matchedStatuses": ["failed"],
            "messageRegex": f"(?s).*\\[{b}\\].*",
        }
        for b, n in CATEGORIES
    ] + [
        {"name": "Other failures", "matchedStatuses": ["failed"]},
        {"name": "Unexpected exceptions", "matchedStatuses": ["broken"]},
    ]
    (results / "categories.json").write_text(json.dumps(categories, indent=1))
    (results / "environment.properties").write_text(
        f"Branch={_git('branch', '--show-current')}\nCommit={_git('rev-parse', '--short', 'HEAD')}\n"
        f"Python={sys.version.split()[0]}\nPorts=Postgres, ThemeParks, Open-Meteo (real)\n"
        "LLM=Anthropic via AnthropicExtractor (injected extractor)\n"
    )
    n_run = 1 + len([d for d in RUNS_DIR.iterdir() if d.name[:17] < run_name])
    stamp = f"{run_name[:10]} {run_name[11:13]}:{run_name[13:15]}"
    passed = counts.get("passed", 0)
    (results / "executor.json").write_text(
        json.dumps(
            {
                "name": "Local e2e",
                "type": "local",
                "buildOrder": n_run,
                "buildName": f"Run {n_run} - {stamp} - {passed}/{len(rows)} passed",
                "reportName": "ParkMind e2e on real ports",
            },
            indent=1,
        )
    )
    shutil.copy(REPORT_DIR / "REPORT.md", run / "REPORT.md")
    final = RUNS_DIR / f"{run_name}_{passed}of{len(rows)}"
    run.rename(final)
    if shutil.which("allure"):
        subprocess.run(
            [
                "allure",
                "generate",
                str(final / "allure-results"),
                "-o",
                str(final / "allure-report"),
                "--clean",
            ],
            check=True,
            capture_output=True,
        )
    latest = RUNS_DIR.parent / "latest"
    latest.unlink(missing_ok=True)
    latest.symlink_to(final)
    return {"scenarios": len(rows), **counts, "run": str(final)}
