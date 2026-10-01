"""PlanDiff — detects what changed between two plans (added/removed/moved
stops), so the agent explains a replan as a delta, not a whole new plan."""

from __future__ import annotations

from typing import Any

from parkmind.core.contracts import Plan
from parkmind.core.contracts import PlanDiff as PlanDiffModel

_COMPARED_FIELDS: tuple[str, ...] = (
    "arrival_time",
    "departure_time",
    "expected_wait_minutes",
    "walking_minutes",
    "utility",
    "served_guests",
    "kind",
)


def _is_different(val1: Any, val2: Any) -> bool:
    """Compare two values with float tolerance to avoid rounding artifacts."""
    if isinstance(val1, float) and isinstance(val2, float):
        return abs(val1 - val2) > 1e-6
    return bool(val1 != val2)


def diff_plans(old_plan: Plan, new_plan: Plan) -> PlanDiffModel:
    """Compare two plan versions and identify added, removed, moved and time-shifted stops.

    Matches §33 PlanDiff fields:
      - stops_added: list of node_ids in new_plan but not in old_plan.
      - stops_removed: list of node_ids in old_plan but not in new_plan.
      - stops_reordered: list of node_ids whose relative order changed.
      - stops_modified: dict mapping node_id to changed fields as (old_value, new_value).

    Guarantees:
      - Deterministic output.
      - Time-only changes are distinguishable from attraction substitutions.
      - Preserves sequence ordering where appropriate.
    """
    old_node_ids = {s.node_id for s in old_plan.stops}
    new_node_ids = {s.node_id for s in new_plan.stops}

    # 1. Added stops (in new_plan but not old_plan, order of appearance)
    seen_added: set[str] = set()
    stops_added: list[str] = []
    for s in new_plan.stops:
        if s.node_id not in old_node_ids and s.node_id not in seen_added:
            stops_added.append(s.node_id)
            seen_added.add(s.node_id)

    # 2. Removed stops (in old_plan but not new_plan, order of appearance)
    seen_removed: set[str] = set()
    stops_removed: list[str] = []
    for s in old_plan.stops:
        if s.node_id not in new_node_ids and s.node_id not in seen_removed:
            stops_removed.append(s.node_id)
            seen_removed.add(s.node_id)

    # 3. Reordered stops (relative position changed among common stops)
    common_old_unique = list(
        dict.fromkeys(s.node_id for s in old_plan.stops if s.node_id in new_node_ids)
    )
    common_new_unique = list(
        dict.fromkeys(s.node_id for s in new_plan.stops if s.node_id in old_node_ids)
    )

    old_index_map = {nid: idx for idx, nid in enumerate(common_old_unique)}
    stops_reordered: list[str] = [
        nid
        for idx, nid in enumerate(common_new_unique)
        if old_index_map.get(nid) != idx
    ]

    # 4. Modified stops (time-shifts, wait, walk, utility, served_guests for common stops)
    old_stops_map: dict[str, Any] = {s.node_id: s for s in old_plan.stops}
    stops_modified: dict[str, dict[str, Any]] = {}

    for s in new_plan.stops:
        nid = s.node_id
        if nid in old_stops_map and nid not in stops_modified:
            old_stop = old_stops_map[nid]
            new_stop = s
            changes: dict[str, tuple[Any, Any]] = {}
            for field in _COMPARED_FIELDS:
                old_val = getattr(old_stop, field)
                new_val = getattr(new_stop, field)
                if _is_different(old_val, new_val):
                    changes[field] = (old_val, new_val)
            if changes:
                stops_modified[nid] = changes

    return PlanDiffModel(
        stops_added=stops_added,
        stops_removed=stops_removed,
        stops_reordered=stops_reordered,
        stops_modified=stops_modified,
    )
