"""Use case: EXPLAIN a plan that already passed the ConstraintChecker.

Explanation happens only after validation: ``execute`` raises
``ExplainBeforeValidationError`` for a result that is not valid. The P0 text is a
deterministic template of the plan (the LLM concierge can replace it later behind
this use case); it reads the plan and never feeds anything back into it.
"""

from collections.abc import Mapping, Sequence

from parkmind.core.contracts import PARK_TZ, CheckResult, Guest, Plan, PlanDiff
from parkmind.services.planning.optimizer import MEAL_VENUE_TBD
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    open_deps,
)


class ExplainBeforeValidationError(RuntimeError):
    """An explanation was requested for a plan the checker has not passed."""


def _guest_label(guest: Guest) -> str:
    number = guest.guest_id.removeprefix("g")
    label = f"Guest {number}" if number.isdigit() else guest.guest_id
    return f"{label} ({guest.height_cm:g} cm)" if guest.height_cm is not None else label


def _skipped_rides(
    plan: Plan,
    names: Mapping[str, str],
    guests: Sequence[Guest],
    height_limits: Mapping[str, int],
) -> list[str]:
    """One line per height-restricted stop that part of the party sits out.

    Reads ``Stop.served_guests`` (who rides) against the restriction; the checker has
    already guaranteed nobody too short is listed as riding.
    """
    lines: list[str] = []
    for stop in plan.stops:
        limit = height_limits.get(stop.node_id)
        if limit is None or not stop.served_guests:
            continue
        skipping = [
            guest
            for guest in guests
            if guest.guest_id not in stop.served_guests
            and (guest.height_cm is None or guest.height_cm < limit)
        ]
        if skipping:
            who = ", ".join(_guest_label(guest) for guest in skipping)
            name = names.get(stop.node_id, stop.node_id)
            lines.append(
                f"{who} will skip {name} (minimum height {limit} cm) and wait outside."
            )
    return lines


def explain_plan(
    plan: Plan,
    check_result: CheckResult,
    names: Mapping[str, str],
    *,
    guests: Sequence[Guest] = (),
    height_limits: Mapping[str, int] | None = None,
) -> str:
    if not check_result.valid:
        raise ExplainBeforeValidationError("a plan is explained only after it passed the checker")
    names = {**names, MEAL_VENUE_TBD: "Lunch (restaurant to be chosen, near your previous stop)"}
    lines = [
        f"{stop.arrival_time.astimezone(PARK_TZ):%H:%M} "
        f"{names.get(stop.node_id, stop.node_id)} "
        f"({stop.expected_wait_minutes:.0f} min wait, {stop.walking_minutes:.0f} min walk)"
        for stop in plan.stops
    ]
    summary = (
        f"{len(plan.stops)} stops, {plan.total_wait_minutes:.0f} min of waiting and "
        f"{plan.total_walking_minutes:.0f} min of walking in total."
    )
    parts = [summary, *lines, *_skipped_rides(plan, names, guests, height_limits or {})]
    if plan.unmet_must_do:
        unmet = ", ".join(names.get(node_id, node_id) for node_id in plan.unmet_must_do)
        parts.append(f"Could not fit your must-dos today: {unmet}.")
    return "\n".join(parts)


def _label_name(label: str, names: Mapping[str, str]) -> str:
    node_id, _, kind = label.partition(":")
    name = names.get(node_id, node_id)
    return f"{name} ({kind.lower()})" if kind else name


def explain_replan(
    plan: Plan,
    check_result: CheckResult,
    names: Mapping[str, str],
    *,
    reason: str,
    diff: PlanDiff,
    guests: Sequence[Guest] = (),
    height_limits: Mapping[str, int] | None = None,
) -> str:
    """``explain_plan`` led by why the plan changed and what changed."""
    changes: list[str] = []
    for title, labels in (
        ("Added", diff.stops_added),
        ("Removed", diff.stops_removed),
        ("Reordered", diff.stops_reordered),
        ("Changed", list(diff.stops_modified)),
    ):
        if labels:
            changes.append(f"{title}: {', '.join(_label_name(label, names) for label in labels)}.")
    if not changes:
        changes.append("No stops changed; only times shifted.")
    body = explain_plan(plan, check_result, names, guests=guests, height_limits=height_limits)
    return "\n".join([reason, *changes, "", body])


def _catalog_facts(deps_factory: DepsFactory) -> tuple[dict[str, str], dict[str, int]]:
    with open_deps(deps_factory) as deps:
        catalog = load_catalog(deps)
    names = {a.node_id: a.name for a in catalog}
    limits = {
        a.node_id: a.height_restriction_cm
        for a in catalog
        if a.height_restriction_cm is not None
    }
    return names, limits


class ExplainPlanUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def execute(
        self, plan: Plan, check_result: CheckResult, *, guests: Sequence[Guest] = ()
    ) -> str:
        if not check_result.valid:
            raise ExplainBeforeValidationError(
                "a plan is explained only after it passed the checker"
            )
        names, limits = _catalog_facts(self._deps_factory)
        return explain_plan(plan, check_result, names, guests=guests, height_limits=limits)

    def execute_replan(
        self,
        plan: Plan,
        check_result: CheckResult,
        *,
        reason: str,
        diff: PlanDiff,
        guests: Sequence[Guest] = (),
    ) -> str:
        if not check_result.valid:
            raise ExplainBeforeValidationError(
                "a plan is explained only after it passed the checker"
            )
        names, limits = _catalog_facts(self._deps_factory)
        return explain_replan(
            plan, check_result, names, reason=reason, diff=diff, guests=guests, height_limits=limits
        )
