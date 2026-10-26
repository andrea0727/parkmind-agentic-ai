"""Turns graph results and state into the plain, JSON-friendly facts the judges read."""

import re
from typing import Any

SECRET = re.compile(r"(postgres(?:ql)?://[^:\s]+:)[^@\s]+@")


def scrub(text: str) -> str:
    return SECRET.sub(r"\1***@", text)


def jsonable(value: Any, depth: int = 0) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if depth > 2:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        return {str(k): jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v, depth + 1) for v in value][:30]
    return f"<{type(value).__name__}>"


def stop_view(stop: Any) -> dict[str, Any]:
    return {
        "kind": stop.kind.value,
        "node_id": str(stop.node_id),
        "arrival": stop.arrival_time.strftime("%H:%M"),
    }


def plan_facts(values: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    check = values.get("check_result")
    if check is not None:
        facts["check_valid"] = check.valid
        facts["violations"] = [
            {
                "rule": v.rule.value,
                "message": scrub(str(getattr(v, "message", v)))[:300],
            }
            for v in check.violations
        ]
    plan = values.get("candidate_plan") or values.get("current_plan")
    if plan is not None:
        facts["n_stops"] = len(plan.stops)
        facts["stops_head"] = [stop_view(s) for s in plan.stops[:4]]
        facts["stop_nodes"] = [str(s.node_id) for s in plan.stops]
        facts["first_arrival"] = (
            plan.stops[0].arrival_time.strftime("%H:%M") if plan.stops else None
        )
        facts["last_arrival"] = (
            plan.stops[-1].arrival_time.strftime("%H:%M") if plan.stops else None
        )
    if values.get("explanation"):
        facts["explanation"] = values["explanation"]
    replies = [m for m in values.get("messages", []) if getattr(m, "type", "") == "ai"]
    if replies:
        facts["last_ai"] = str(replies[-1].content)
    return facts


def interrupt_view(result: dict[str, Any]) -> dict[str, Any] | None:
    pending = result.get("__interrupt__")
    if not pending:
        return None
    value = pending[0].value
    return {
        "kind": value.get("kind"),
        "payload": jsonable({k: v for k, v in value.items() if k != "candidate_plan"}),
    }


def graph_state(graph: Any, config: Any) -> dict[str, Any]:
    """The thread's values; while paused inside the planning subgraph, that subgraph's."""
    snapshot = graph.get_state(config, subgraphs=True)
    for task in snapshot.tasks:
        if task.state is not None and hasattr(task.state, "values"):
            return task.state.values
    return snapshot.values


def attraction_rows(url: str) -> int | None:
    import psycopg

    try:
        with psycopg.connect(url) as connection:
            row = connection.execute("select count(*) from attractions").fetchone()
            return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def height_restricted_nodes(url: str) -> list[str]:
    """Node ids of the catalog attractions that have a minimum height."""
    import psycopg

    try:
        with psycopg.connect(url) as connection:
            rows = connection.execute(
                "select node_id from attractions "
                "where (payload->>'height_restriction_cm')::int > 0 order by node_id"
            ).fetchall()
            return [row[0] for row in rows]
    except Exception:  # noqa: BLE001
        return []
