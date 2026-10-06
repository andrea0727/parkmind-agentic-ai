"""PlanDiff — detects what changed between two plans (added, removed,
reordered and modified stops), so the agent explains a replan as a delta,
not a whole new plan.

Follows §33 PlanDiff contract:
  - stops_added: list of added stop identifiers (e.g. 'node_id' or 'node_id:KIND').
  - stops_removed: list of removed stop identifiers.
  - stops_reordered: list of stop identifiers whose relative order in sequence changed.
  - stops_modified: dict mapping stop identifier to changed fields as [old_val, new_val]
    in JSON-native format (ISO strings for datetimes, string values for enums).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from parkmind.core.contracts import Plan, Stop, StopKind
from parkmind.core.contracts import PlanDiff as PlanDiffModel

__all__ = ["diff_plans"]

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


def _json_serialize_value(val: Any) -> Any:
    """Convert values to JSON-native primitives for persistence compatibility."""
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, tuple):
        return [_json_serialize_value(item) for item in val]
    if isinstance(val, list):
        return [_json_serialize_value(item) for item in val]
    return val


def _stop_key(stop: Stop) -> tuple[str, StopKind]:
    """Unique conceptual identity tuple for sequence alignment."""
    return (stop.node_id, stop.kind)


def _stop_label(stop: Stop) -> str:
    """User-facing stop identifier in diff outputs.

    Attractions use 'node_id' directly (e.g. 'space-mountain').
    Non-attractions (REST, MEAL, SHOW) append their kind (e.g. 'node_id:REST')
    to prevent key collisions when multiple stops share the same node.
    """
    if stop.kind == StopKind.ATTRACTION:
        return stop.node_id
    return f"{stop.node_id}:{stop.kind.value}"


def _longest_increasing_subsequence_indices(seq: list[int]) -> set[int]:
    """Return the set of indices in seq that belong to the longest increasing subsequence.

    Uses dynamic programming with deterministic canonical resolution.
    """
    if not seq:
        return set()
    n = len(seq)
    lengths = [1] * n
    parents = [-1] * n
    for i in range(n):
        for j in range(i):
            if seq[j] < seq[i] and lengths[j] + 1 > lengths[i]:
                lengths[i] = lengths[j] + 1
                parents[i] = j

    max_len = max(lengths)
    end_idx = lengths.index(max_len)

    lis_indices: set[int] = set()
    curr = end_idx
    while curr != -1:
        lis_indices.add(curr)
        curr = parents[curr]
    return lis_indices


def diff_plans(old_plan: Plan, new_plan: Plan) -> PlanDiffModel:
    """Compare two plan versions and identify added, removed, moved and time-shifted stops.

    Matches §33 PlanDiff fields:
      - stops_added: stop identifiers in new_plan exceeding old_plan occurrences.
      - stops_removed: stop identifiers in old_plan exceeding new_plan occurrences.
      - stops_reordered: stop identifiers whose relative sequence position changed.
      - stops_modified: dict mapping stop identifier to changed fields as [old_val, new_val].

    Guarantees:
      - Deterministic output.
      - Identical plans (including those with RESTs/duplicate nodes) produce empty diff.
      - Time-only changes are distinguishable from attraction substitutions.
      - JSON-native values ensure round-trip persistence in Postgres and SessionStore.
    """
    old_stops = old_plan.stops
    new_stops = new_plan.stops

    old_keys = [_stop_key(s) for s in old_stops]
    new_keys = [_stop_key(s) for s in new_stops]

    # Map key -> list of indices in appearance order
    old_key_indices: dict[tuple[str, StopKind], list[int]] = {}
    for idx, key in enumerate(old_keys):
        old_key_indices.setdefault(key, []).append(idx)

    new_key_indices: dict[tuple[str, StopKind], list[int]] = {}
    for idx, key in enumerate(new_keys):
        new_key_indices.setdefault(key, []).append(idx)

    # 1. Added & Removed counts
    old_counts: dict[tuple[str, StopKind], int] = {}
    for k in old_keys:
        old_counts[k] = old_counts.get(k, 0) + 1

    new_counts: dict[tuple[str, StopKind], int] = {}
    for k in new_keys:
        new_counts[k] = new_counts.get(k, 0) + 1

    # Removed: occurrences in old exceeding new
    stops_removed: list[str] = []
    removed_remaining = dict(old_counts)
    for s in old_stops:
        k = _stop_key(s)
        curr_new_count = new_counts.get(k, 0)
        if removed_remaining[k] > curr_new_count:
            stops_removed.append(_stop_label(s))
            removed_remaining[k] -= 1

    # Added: occurrences in new exceeding old
    stops_added: list[str] = []
    added_remaining = dict(new_counts)
    for s in new_stops:
        k = _stop_key(s)
        curr_old_count = old_counts.get(k, 0)
        if added_remaining[k] > curr_old_count:
            stops_added.append(_stop_label(s))
            added_remaining[k] -= 1

    # 2. Pair up common stops in FIFO appearance order
    paired_old_to_new: dict[int, int] = {}
    paired_new_to_old: dict[int, int] = {}

    old_cursor: dict[tuple[str, StopKind], int] = {k: 0 for k in old_counts}
    for new_idx, s in enumerate(new_stops):
        k = _stop_key(s)
        if k in old_key_indices:
            cursor = old_cursor[k]
            if cursor < len(old_key_indices[k]):
                old_idx = old_key_indices[k][cursor]
                paired_old_to_new[old_idx] = new_idx
                paired_new_to_old[new_idx] = old_idx
                old_cursor[k] += 1

    # 3. Reordered stops (common stops outside the Longest Increasing Subsequence)
    common_new_indices = sorted(paired_new_to_old.keys())
    mapped_old_positions = [paired_new_to_old[n_idx] for n_idx in common_new_indices]

    lis_set = _longest_increasing_subsequence_indices(mapped_old_positions)
    stops_reordered: list[str] = []
    seen_reordered: set[str] = set()
    for pos_in_common, n_idx in enumerate(common_new_indices):
        if pos_in_common not in lis_set:
            label = _stop_label(new_stops[n_idx])
            if label not in seen_reordered:
                stops_reordered.append(label)
                seen_reordered.add(label)

    # 4. Modified stops (paired common stops with field changes)
    stops_modified: dict[str, dict[str, Any]] = {}
    for old_idx, new_idx in paired_old_to_new.items():
        old_stop = old_stops[old_idx]
        new_stop = new_stops[new_idx]
        label = _stop_label(old_stop)

        changes: dict[str, Any] = {}
        for field in _COMPARED_FIELDS:
            old_val = getattr(old_stop, field)
            new_val = getattr(new_stop, field)
            if _is_different(old_val, new_val):
                changes[field] = [
                    _json_serialize_value(old_val),
                    _json_serialize_value(new_val),
                ]

        if changes:
            if label in stops_modified:
                stops_modified[f"{label}#{old_idx}"] = changes
            else:
                stops_modified[label] = changes

    return PlanDiffModel(
        stops_added=stops_added,
        stops_removed=stops_removed,
        stops_reordered=stops_reordered,
        stops_modified=stops_modified,
    )
