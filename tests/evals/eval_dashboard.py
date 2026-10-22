"""Static HTML dashboard of the real conversations: fixed template vs LLM question writer, with the transcripts behind every number."""

from collections import defaultdict
from html import escape
from pathlib import Path

from evals.eval_live import ARMS, FIXED, LLM
from evals.eval_report import Result
from evals.eval_runs import meets, pass_rate
from evals.eval_thresholds import JUDGED, PASS_RATE

DASHBOARD = Path("build/evals/dashboard.html")
LABEL = {FIXED: "Plantilla fija", LLM: "LLM redacta la pregunta"}
CLASS = {FIXED: "fixed", LLM: "llm"}

CSS = """
:root{--bg:#fff;--panel:#f6f7f9;--ink:#15181d;--ink2:#4a5260;--muted:#7b8494;--line:#dfe3ea;
--llm:#2a78d6;--fixed:#eb6834;--good:#1a7f4b;--bad:#c0392b;--good-bg:#e4f4ea;--bad-bg:#fbe9e6}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--panel:#1c2027;--ink:#eceff4;--ink2:#b4bcc9;
--muted:#8892a3;--line:#2c323c;--llm:#5aa0f0;--fixed:#f08a5d;--good:#4cc38a;--bad:#ff7b6b;--good-bg:#17301f;--bad-bg:#3a1c19}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 10px}p.sub{color:var(--ink2);margin:0 0 18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{color:var(--ink2);font-size:13px}.card .v{font-size:30px;font-weight:650;margin:2px 0}
.card .n{color:var(--muted);font-size:12px}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
.llm .dot,.sw.llm{background:var(--llm)}.fixed .dot,.sw.fixed{background:var(--fixed)}
.legend{display:flex;gap:18px;margin:6px 0 10px;color:var(--ink2);font-size:13px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
svg{width:100%;height:auto;display:block}svg text{fill:var(--ink2);font:12px system-ui,sans-serif}
svg .val{fill:var(--ink);font-weight:600}svg .grid{stroke:var(--line)}svg rect.llm{fill:var(--llm)}svg rect.fixed{fill:var(--fixed)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink2);font-weight:600}.ok{color:var(--good)}.no{color:var(--bad)}
details{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:8px 0}
summary{cursor:pointer;font-weight:600}.turn{margin:6px 0}.who{color:var(--muted);font-size:12px;text-transform:uppercase}
.why{color:var(--ink2);font-size:12px}code{font-size:12px}
.verdict{display:flex;gap:14px;align-items:center;border-radius:10px;padding:14px 18px;margin:0 0 16px;border:1px solid var(--line)}
.verdict.pass{background:var(--good-bg);border-color:var(--good)}.verdict.fail{background:var(--bad-bg);border-color:var(--bad)}
.verdict .big{font-size:22px;font-weight:700}.verdict.pass .big{color:var(--good)}.verdict.fail .big{color:var(--bad)}
.badge{display:inline-block;font-weight:650;font-size:13px;margin-top:4px}.how{color:var(--ink2);font-size:13px;margin:6px 0 18px;padding-left:18px}
"""


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _status(ok: bool) -> str:
    return '<span class="ok">✓ pasa</span>' if ok else '<span class="no">✗ falla</span>'


def _latest_batch(results: list[Result]) -> list[Result]:
    """The newest batch of each arm, so rerunning one arm does not hide the other."""
    latest = {
        arm: max(r.batch for r in results if r.arm == arm)
        for arm in {r.arm for r in results}
    }
    return [r for r in results if r.batch == latest[r.arm]]


def approved(batch: list[Result], arm: str) -> bool:
    """An arm is approved when every dialogue reaches the target pass rate."""
    mine = [r for r in batch if r.arm == arm]
    dialogues = {r.dialogue for r in mine}
    return bool(mine) and all(
        meets([r.passed for r in mine if r.dialogue == d]) for d in dialogues
    )


def _verdict(batch: list[Result]) -> str:
    arm = LLM if any(r.arm == LLM for r in batch) else batch[0].arm
    mine = [r for r in batch if r.arm == arm]
    ok = approved(batch, arm)
    passed = sum(r.passed for r in mine)
    failing = sorted(
        {
            d
            for d in {r.dialogue for r in mine}
            if not meets([r.passed for r in mine if r.dialogue == d])
        }
    )
    detail = (
        f"{passed} de {len(mine)} conversaciones pasaron ({_pct(pass_rate([r.passed for r in mine]))}); "
        f"la meta es {_pct(PASS_RATE)} en cada tipo de conversación."
        + (f" No llegan a la meta: {', '.join(failing)}." if failing else "")
    )
    return (
        f'<div class="verdict {"pass" if ok else "fail"}"><div class="big">'
        f"{'✓ APROBADO' if ok else '✗ NO APROBADO'}</div>"
        f"<div><strong>{LABEL[arm]}</strong><br>{detail}</div></div>"
    )


def _cards(batch: list[Result]) -> str:
    cards = []
    for arm in ARMS:
        mine = [r for r in batch if r.arm == arm]
        if not mine:
            continue
        cards.append(
            f'<div class="card {CLASS[arm]}"><div class="k"><span class="dot"></span>{LABEL[arm]}</div>'
            f'<div class="v">{_pct(pass_rate([r.passed for r in mine]))}</div>'
            f'<div class="badge">{_status(approved(batch, arm)).replace("pasa", "APROBADO").replace("falla", "NO APROBADO")}</div>'
            f'<div class="n">de {len(mine)} conversaciones pasan · naturalidad media {_pct(sum(r.naturalness for r in mine) / len(mine))} · '
            f"sin repetir preguntas {_pct(pass_rate([r.flow_passed for r in mine]))}</div></div>"
        )
    return f'<div class="cards">{"".join(cards)}</div>'


def _bars(batch: list[Result]) -> str:
    dialogues = sorted({r.dialogue for r in batch})
    width, height, left, top, bottom = 960, 260, 44, 16, 44
    plot_h = height - top - bottom
    group_w = (width - left) / max(len(dialogues), 1)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Conversaciones aprobadas por tipo de conversación y brazo">'
    ]
    for tick in (0, 0.5, 1.0):
        y = top + plot_h * (1 - tick)
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width}" y1="{y}" y2="{y}"/><text x="{left - 8}" y="{y + 4}" text-anchor="end">{_pct(tick)}</text>'
        )
    y = top + plot_h * (1 - PASS_RATE)
    parts.append(
        f'<line x1="{left}" x2="{width}" y1="{y}" y2="{y}" stroke="var(--muted)" stroke-dasharray="4 4"/><text x="{width}" y="{y - 4}" text-anchor="end">meta {_pct(PASS_RATE)}</text>'
    )
    bar_w = min(70, group_w / 3)
    for i, dialogue in enumerate(dialogues):
        cx = left + group_w * (i + 0.5)
        for j, arm in enumerate(ARMS):
            mine = [r for r in batch if r.arm == arm and r.dialogue == dialogue]
            if not mine:
                continue
            rate = pass_rate([r.passed for r in mine])
            x = cx + (j - 1) * (bar_w + 2) + 1
            h = max(plot_h * rate, 2)
            parts.append(
                f'<rect class="{CLASS[arm]}" x="{x}" y="{top + plot_h - h}" width="{bar_w}" height="{h}" rx="4">'
                f"<title>{LABEL[arm]} · {dialogue}: {_pct(rate)} de {len(mine)} conversaciones</title></rect>"
                f'<text class="val" x="{x + bar_w / 2}" y="{top + plot_h - h - 5}" text-anchor="middle">{_pct(rate)}</text>'
            )
        parts.append(
            f'<text x="{cx}" y="{height - 18}" text-anchor="middle">{escape(dialogue)}</text>'
        )
    parts.append("</svg>")
    legend = "".join(
        f'<span><i class="sw {CLASS[a]}"></i>{LABEL[a]}</span>' for a in ARMS
    )
    return f'<div class="legend">{legend}</div>{"".join(parts)}'


def _history(results: list[Result]) -> str:
    groups: dict[tuple[str, str, str], list[Result]] = defaultdict(list)
    for r in results:
        groups[(r.batch, r.prompt_version, r.arm)].append(r)
    rows = "".join(
        f"<tr><td>{escape(batch)}</td><td>{escape(version)}</td><td>{LABEL[arm]}</td>"
        f"<td>{len(rs)}</td><td>{_pct(pass_rate([r.passed for r in rs]))}</td>"
        f"<td>{_pct(sum(r.naturalness for r in rs) / len(rs))}</td></tr>"
        for (batch, version, arm), rs in sorted(groups.items())
    )
    return (
        "<table><thead><tr><th>Lote</th><th>Prompt</th><th>Brazo</th><th>Conversaciones</th>"
        f"<th>Aprobadas</th><th>Naturalidad media</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _transcripts(batch: list[Result]) -> str:
    out = []
    for r in sorted(batch, key=lambda r: (r.dialogue, r.arm, r.run_id)):
        scores = {t.turn: t for t in r.turns}
        lines, turn_no = [], 0
        for role, text in r.transcript:
            if role == "guest":
                lines.append(
                    f'<div class="turn"><span class="who">invitado</span><br>{escape(text)}</div>'
                )
                continue
            turn_no += 1
            t = scores.get(turn_no)
            verdict = f" {_status(t.passed)} {_pct(t.score)}" if t else ""
            why = f'<div class="why">{escape(t.reason)}</div>' if t else ""
            lines.append(
                f'<div class="turn"><span class="who">asistente{verdict}</span><br>{escape(text)}{why}</div>'
            )
        out.append(
            f"<details><summary>{_status(r.passed)} {escape(r.dialogue)} · {LABEL[r.arm]} · "
            f"<code>{escape(r.run_id)}</code></summary>{''.join(lines)}"
            f'<div class="why">flujo: {escape(r.flow_reason)}</div></details>'
        )
    return "".join(out)


def render(results: list[Result]) -> str:
    if not results:
        return "<!doctype html><title>ParkMind evals</title><p>Aún no hay resultados. Ejecuta python -m evals.run_live.</p>"
    batch = _latest_batch(results)
    sample = next((r for r in batch if r.arm == LLM), batch[0])
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ParkMind evals</title><style>{CSS}</style></head><body><main>
<h1>Calidad de la conversación de ParkMind</h1>
<p class="sub">Conversaciones reales con el modelo, calificadas turno a turno por un juez independiente. Último lote: prompt <code>{escape(sample.prompt_version)}</code>,
generador <code>{escape(sample.generator_model)}</code>, juez <code>{escape(sample.judge_model)}</code>.</p>
{_verdict(batch)}
<ul class="how"><li>Una conversación <strong>pasa</strong> si ninguna pregunta se repite, el asistente no repite lo que dijo el invitado y cada turno saca al menos {_pct(JUDGED)} del juez.</li>
<li>Un brazo queda <strong>aprobado</strong> si, en cada tipo de conversación (clear, english, unsure), pasa al menos {_pct(PASS_RATE)} de las conversaciones.</li>
<li>Se compara la plantilla fija (el respaldo sin LLM: una pregunta por turno, con reacción y opciones) con el LLM redactando la pregunta.</li></ul>
{_cards(batch)}
<h2>Conversaciones aprobadas por tipo de conversación</h2>{_bars(batch)}
<h2>Historial por versión de prompt</h2>{_history(results)}
<h2>Transcripciones del último lote</h2>{_transcripts(batch)}
</main></body></html>"""


def write(results: list[Result], path: Path = DASHBOARD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(results), encoding="utf-8")
    return path
