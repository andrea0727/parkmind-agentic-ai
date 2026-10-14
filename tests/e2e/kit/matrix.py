"""The scenario-to-layer matrix, generated from the catalog so it cannot drift from it."""

from collections.abc import Sequence

from e2e.model import LAYERS, Scenario

TITLES = {
    "e2e": "E2E",
    "workflow": "Workflow",
    "llm_eval": "LLM eval",
    "observability": "Observability",
    "business_rules": "Business rules",
}


def render_matrix(scenarios: Sequence[Scenario]) -> str:
    ordered = sorted(scenarios, key=lambda s: s.number)
    lines = [
        "# Scenario matrix",
        "",
        (
            "Generated from `tests/e2e/catalog` by `python -m e2e.cli matrix`; "
            "`tests/e2e/test_catalog.py` fails when this file is out of date. Do not edit it by hand."
        ),
        "",
        "A mark means the scenario carries a criterion at that layer. The criteria follow the table.",
        "",
        "| # | Scenario | Area | Severity | "
        + " | ".join(TITLES[x] for x in LAYERS)
        + " |",
        "|---|---|---|---|" + "---|" * len(LAYERS),
    ]
    for s in ordered:
        covered = s.layers()
        marks = " | ".join("x" if layer in covered else "" for layer in LAYERS)
        lines.append(f"| {s.number} | `{s.id}` | {s.area} | {s.severity} | {marks} |")
    lines += ["", "## Criteria per scenario", ""]
    for s in ordered:
        lines += [f"### {s.number}. {s.title} (`{s.id}`)", ""]
        lines.append(
            f"- **E2E:** {s.expected}" if s.live else "- **E2E:** not run live"
        )
        for layer in LAYERS[1:]:
            text = getattr(s.criteria, layer)
            if text:
                lines.append(f"- **{TITLES[layer]}:** {text}")
        if s.offline is not None and not s.criteria.workflow:
            lines.append(
                "- **Workflow:** replayed offline against the scenario's judge"
            )
        if s.criteria.refs:
            lines.append(
                "- **Tests:** " + ", ".join(f"`tests/{ref}`" for ref in s.criteria.refs)
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
