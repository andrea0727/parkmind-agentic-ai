"""Run every e2e scenario against real ports and store raw facts per scenario in docs/e2e/raw/<id>.json.

Usage: python scripts/e2e/capture.py [scenario ...]    (FORCE=1 re-runs already captured scenarios)
Needs real credentials; see scripts/e2e/README.md.
"""

import contextlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from typing import Any

import harness as h
import scenarios as sc
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from parkmind.core.contracts import PARK_TZ
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph.checkpointing import default_checkpointer

SECRET = re.compile(r"(postgres(?:ql)?://[^:\s]+:)[^@\s]+@")


def scrub(s: str) -> str:
    return SECRET.sub(r"\1***@", s)


def jsonable(v: Any, depth: int = 0) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if depth > 2:
        return f"<{type(v).__name__}>"
    if isinstance(v, dict):
        return {str(k): jsonable(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x, depth + 1) for x in v][:30]
    return f"<{type(v).__name__}>"


def stop_view(s: Any) -> dict[str, Any]:
    return {"kind": s.kind.value, "node_id": str(s.node_id), "arrival": s.arrival_time.strftime("%H:%M")}


def plan_facts(vals: dict[str, Any]) -> dict[str, Any]:
    f: dict[str, Any] = {}
    cr = vals.get("check_result")
    if cr is not None:
        f["check_valid"] = cr.valid
        f["violations"] = [{"rule": v.rule.value, "message": scrub(str(getattr(v, "message", v)))[:300]} for v in cr.violations]
    plan = vals.get("candidate_plan") or vals.get("current_plan")
    if plan is not None:
        f["n_stops"] = len(plan.stops)
        f["stops_head"] = [stop_view(s) for s in plan.stops[:4]]
        f["first_arrival"] = plan.stops[0].arrival_time.strftime("%H:%M") if plan.stops else None
        f["last_arrival"] = plan.stops[-1].arrival_time.strftime("%H:%M") if plan.stops else None
    if vals.get("explanation"):
        f["explanation"] = vals["explanation"]
    ai = [m for m in vals.get("messages", []) if getattr(m, "type", "") == "ai"]
    if ai:
        f["last_ai"] = str(ai[-1].content)
    return f


def interrupt_view(res: dict[str, Any]) -> dict[str, Any] | None:
    intr = res.get("__interrupt__")
    if not intr:
        return None
    val = intr[0].value
    return {"kind": val.get("kind"), "payload": jsonable({k: v for k, v in val.items() if k != "candidate_plan"})}


def attraction_rows(url: str) -> int | None:
    import psycopg

    try:
        with psycopg.connect(url) as c:
            row = c.execute("select count(*) from attractions").fetchone()
            return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def run_once(name: str, scenario: dict[str, Any]) -> dict[str, Any]:
    h.EVENTS.clear()
    h.LLM_CALLS.clear()
    thread = f"{name}-{uuid.uuid4().hex[:6]}"
    confirm = scenario.get("confirm", h.CONFIRM)
    approval = scenario.get("approval", {"decision": "APPROVED"})
    out: dict[str, Any] = {
        "id": name, "messages": scenario["msgs"], "confirm_payload": jsonable(confirm), "approval_payload": jsonable(approval),
        "setup": getattr(scenario.get("ctx"), "__name__", None) if "ctx" in scenario else None,
        "now": datetime.now(PARK_TZ).strftime("%H:%M"), "steps": [],
    }
    graph = ipg.build_initial_planning_graph(h.NvidiaExtractor(), None, default_checkpointer())
    config: Any = {"configurable": {"thread_id": thread}}
    cm = scenario["ctx"]() if "ctx" in scenario else contextlib.nullcontext()
    rows_before = attraction_rows(sc.EDGE_URL) if name == "happy_catalogo_vacio" else None
    t0 = time.time()
    try:
        with cm:
            res = graph.invoke({"thread_id": thread, "messages": [HumanMessage(content=m) for m in scenario["msgs"]]}, config=config)
            out["llm_calls"] = len(h.LLM_CALLS)
            if h.LLM_CALLS:
                a = h.LLM_CALLS[0]["calls"][0]["args"] if h.LLM_CALLS[0]["calls"] else {}
                out["extracted"] = {k: a.get(k) for k in ("party_size", "must_do", "avoid", "departure_time", "lunch_window")}
                out["extracted"]["heights"] = [g.get("height_cm") for g in a.get("guests", [])]
                out["extracted"]["n_guests"] = len(a.get("guests", []))
            out["stages_elicit"] = list(h.EVENTS)
            i1 = interrupt_view(res)
            out["interrupt1"] = i1
            out["facts1"] = plan_facts(h.graph_state(graph, config))
            if i1 and i1["kind"] == "hard_constraint_confirmation":
                h.EVENTS.clear()
                res = graph.invoke(Command(resume=confirm), config=config)
                out["stages"] = list(h.EVENTS)
                out["interrupt2"] = interrupt_view(res)
                out["facts2"] = plan_facts(h.graph_state(graph, config))
                if out["interrupt2"] and out["interrupt2"]["kind"] == "plan_approval":
                    h.EVENTS.clear()
                    res = graph.invoke(Command(resume=approval), config=config)
                    out["stages_after_approval"] = list(h.EVENTS)
                    st = h.graph_state(graph, config)
                    out["approval"] = jsonable(st.get("approval"))
                    out["plan_active"] = st.get("current_plan") is not None
                    out["interrupt3"] = interrupt_view(res)
    except Exception as exc:  # noqa: BLE001
        out["exception"] = {"type": type(exc).__name__, "message": scrub(str(exc))[:500]}
    if rows_before is not None:
        out["attraction_rows"] = {"before": rows_before, "after": attraction_rows(sc.EDGE_URL)}
    out["seconds"] = round(time.time() - t0, 1)
    return out


def run(name: str) -> None:
    target = h.RAW_DIR / f"{name}.json"
    if target.exists() and not os.environ.get("FORCE"):
        print(f"skip {name} (already captured)", flush=True)
        return
    scenario = sc.SCENARIOS[name]
    out = run_once(name, scenario)
    if "exception" in out and "Internal server error" in out["exception"]["message"]:
        out = run_once(name, scenario)
        out["retried_after_llm_500"] = True
    target.write_text(json.dumps(out, indent=2, default=str))
    print(f"done {name} in {out['seconds']}s exc={out.get('exception', {}).get('type')}", flush=True)


def main() -> None:
    h.RAW_DIR.mkdir(parents=True, exist_ok=True)
    h.install_stage_spies()
    for name in sys.argv[1:] or sc.ORDER:
        run(name)


if __name__ == "__main__":
    main()
