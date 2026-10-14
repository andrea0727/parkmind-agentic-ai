"""Use case: EXPLAIN a plan that already passed the ConstraintChecker.

Explanation happens only after validation: ``prepare`` raises
``ExplainBeforeValidationError`` for a result that is not valid. It reads the plan
and never feeds anything back into it.

``prepare`` gives the EXPLAIN step (``agents/explain_agent.py``, P0-31) both halves
of section 40's contract:

* the **payload** -- the only data the LLM may explain from: the validated plan,
  the diff and reason of a replan, the data the plan was built on (provenance) and
  the preference weights that drove it, each labelled ``stated``, ``learned`` or
  ``default`` [C17]. Names, never node ids (an id's digits would let numbers through
  the grounding check), and every figure already rounded the way it is shown;
* the **template** -- the deterministic text, the fallback when the LLM's text is
  not grounded. It prints only figures that are in the payload, plus the guests'
  own heights, which it shows them but which never go to the LLM.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

from parkmind.core.contracts import (
    PARK_TZ,
    CheckResult,
    GroupObjective,
    Guest,
    Plan,
    PlanDiff,
)
from parkmind.core.language import DEFAULT_LANGUAGE, Language, text
from parkmind.services.planning.optimizer import MEAL_VENUE_TBD
from parkmind.services.use_cases.planning_deps import (
    DepsFactory,
    default_planning_deps,
    load_catalog,
    open_deps,
)


class ExplainBeforeValidationError(RuntimeError):
    """An explanation was requested for a plan the checker has not passed."""


def _require_valid(check_result: CheckResult) -> None:
    """Explanation comes only after validation (section 40)."""
    if not check_result.valid:
        raise ExplainBeforeValidationError("a plan is explained only after it passed the checker")


def _guest_ref(guest: Guest, lang: Language = DEFAULT_LANGUAGE) -> str:
    """``Guest 2``: the neutral name, safe to send to the narrator."""
    number = guest.guest_id.removeprefix("g")
    return text("guest_label", lang, number=number) if number.isdigit() else guest.guest_id


def _guest_name(guest: Guest, lang: Language = DEFAULT_LANGUAGE) -> str:
    """The guest's own label ("tu mamá") for the template, which stays in-process."""
    return guest.label or _guest_ref(guest, lang)


def _guest_label(guest: Guest, lang: Language) -> str:
    """The template's label: the guests see their own height (``Guest 2 (110 cm)``)."""
    label = _guest_name(guest, lang)
    return f"{label} ({guest.height_cm:g} cm)" if guest.height_cm is not None else label


def _skipped_rides(
    plan: Plan,
    names: Mapping[str, str],
    guests: Sequence[Guest],
    height_limits: Mapping[str, int],
    lang: Language,
) -> list[str]:
    """One line per height-restricted stop that part of the party sits out.

    Reads ``Stop.served_guests`` (who rides) against the restriction; the checker has
    already guaranteed nobody too short is listed as riding.
    """
    lines: list[str] = []
    for node_id, limit, skipping in _sitting_out(plan, guests, height_limits):
        who = ", ".join(_guest_label(guest, lang) for guest in skipping)
        name = names.get(node_id, node_id)
        lines.append(text("plan_skip", lang, who=who, name=name, limit=limit))
    return lines


def _sitting_out(
    plan: Plan, guests: Sequence[Guest], height_limits: Mapping[str, int]
) -> list[tuple[str, int, list[Guest]]]:
    """``(node_id, minimum height, guests who sit it out)`` per height-restricted stop."""
    out: list[tuple[str, int, list[Guest]]] = []
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
            out.append((stop.node_id, limit, skipping))
    return out


def explain_plan(
    plan: Plan,
    check_result: CheckResult,
    names: Mapping[str, str],
    *,
    guests: Sequence[Guest] = (),
    height_limits: Mapping[str, int] | None = None,
    lang: Language = DEFAULT_LANGUAGE,
) -> str:
    _require_valid(check_result)
    solo = len(guests) == 1
    names = {**names, MEAL_VENUE_TBD: text("plan_lunch_tbd", lang, solo=solo)}
    lines = [
        text(
            "plan_stop",
            lang,
            time=f"{stop.arrival_time.astimezone(PARK_TZ):%H:%M}",
            name=names.get(stop.node_id, stop.node_id),
            wait=f"{stop.expected_wait_minutes:.0f}",
            walk=f"{stop.walking_minutes:.0f}",
        )
        for stop in plan.stops
    ]
    summary = text(
        "plan_summary",
        lang,
        stops=len(plan.stops),
        wait=f"{plan.total_wait_minutes:.0f}",
        walk=f"{plan.total_walking_minutes:.0f}",
    )
    parts = [
        summary,
        *lines,
        *_skipped_rides(plan, names, guests, height_limits or {}, lang),
    ]
    if plan.unmet_must_do:
        unmet = ", ".join(names.get(node_id, node_id) for node_id in plan.unmet_must_do)
        parts.append(text("plan_unmet", lang, solo=solo, names=unmet))
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
    lang: Language = DEFAULT_LANGUAGE,
) -> str:
    """``explain_plan`` led by why the plan changed and what changed."""
    changes: list[str] = []
    for key, labels in (
        ("diff_added", diff.stops_added),
        ("diff_removed", diff.stops_removed),
        ("diff_reordered", diff.stops_reordered),
        ("diff_changed", list(diff.stops_modified)),
    ):
        if labels:
            items = ", ".join(_label_name(label, names) for label in labels)
            changes.append(text("diff_line", lang, title=text(key, lang), items=items))
    if not changes:
        changes.append(text("diff_times_only", lang))
    body = explain_plan(
        plan, check_result, names, guests=guests, height_limits=height_limits, lang=lang
    )
    lead = [reason] if reason else []
    return "\n".join([*lead, *changes, "", body])


@dataclass(frozen=True)
class GuestSittingOut:
    guest: str
    """``Guest 2`` -- never the guest's height or their free-text label ("tu mamá"):
    the narrator does not need either to say they sit a ride out (section 12)."""
    minimum_height_cm: int
    """The ride's posted minimum: park data, not the guest's."""


@dataclass(frozen=True)
class StopFacts:
    name: str
    kind: str
    arrives: str
    """``HH:MM``, park time."""
    leaves: str
    wait_minutes: int
    walk_minutes: int
    must_do: bool
    preference_score: float
    """The stop's utility for this party, 2 decimals."""
    sitting_out: list[GuestSittingOut]


@dataclass(frozen=True)
class PreferenceSignal:
    weight: str
    value: float
    """2 decimals."""
    source: str
    """``stated`` (the guests said it), ``learned`` (from their behavior) or ``default``."""


@dataclass(frozen=True)
class ExplanationPayload:
    """Everything the EXPLAIN step may say; every figure it may use (section 40)."""

    stops: list[StopFacts]
    total_stops: int
    total_wait_minutes: int
    total_walking_minutes: int
    must_do_in_plan: list[str]
    unmet_must_do: list[str]
    """Must-dos that could not fit today (unavailable for the whole horizon)."""
    preferences: list[PreferenceSignal]
    data: dict[str, str | list[str]]
    """When the park data was read and from where, and the strategies used."""
    reason: str | None = None
    """Why the plan changed (a replan)."""
    changes: dict[str, list[str]] | None = None
    """``added`` / ``removed`` / ``reordered`` / ``changed`` stop names (a replan)."""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)


@dataclass(frozen=True)
class PreparedExplanation:
    payload: ExplanationPayload
    template: str
    """The deterministic text: the fallback, built from the same facts."""


def _minutes(value: float) -> int:
    """Rounded exactly as the template prints it (``{value:.0f}``)."""
    return int(f"{value:.0f}")


def _clock(moment: datetime) -> str:
    return f"{moment.astimezone(PARK_TZ):%H:%M}"


def explanation_payload(
    plan: Plan,
    check_result: CheckResult,
    names: Mapping[str, str],
    *,
    guests: Sequence[Guest] = (),
    height_limits: Mapping[str, int] | None = None,
    must_do: Sequence[str] = (),
    objective: GroupObjective | None = None,
    reason: str | None = None,
    diff: PlanDiff | None = None,
) -> ExplanationPayload:
    _require_valid(check_result)
    names = {
        **names,
        MEAL_VENUE_TBD: "Lunch (restaurant to be chosen, near your previous stop)",
    }
    out_by_stop = {
        node_id: [
            GuestSittingOut(guest=_guest_ref(guest), minimum_height_cm=limit) for guest in skipping
        ]
        for node_id, limit, skipping in _sitting_out(plan, guests, height_limits or {})
    }
    wanted = set(must_do)
    stops = [
        StopFacts(
            name=names.get(stop.node_id, stop.node_id),
            kind=stop.kind.value,
            arrives=_clock(stop.arrival_time),
            leaves=_clock(stop.departure_time),
            wait_minutes=_minutes(stop.expected_wait_minutes),
            walk_minutes=_minutes(stop.walking_minutes),
            must_do=stop.node_id in wanted,
            preference_score=round(stop.utility, 2),
            sitting_out=out_by_stop.get(stop.node_id, []),
        )
        for stop in plan.stops
    ]
    planned = {stop.node_id for stop in plan.stops}
    preferences = (
        [
            PreferenceSignal(
                weight=weight, value=round(value, 2), source=_source(objective, weight)
            )
            for weight, value in sorted(objective.weights.items())
        ]
        if objective is not None
        else []
    )
    provenance = plan.provenance
    changes = None
    if diff is not None:
        changes = {
            title: [_label_name(label, names) for label in labels]
            for title, labels in (
                ("added", diff.stops_added),
                ("removed", diff.stops_removed),
                ("reordered", diff.stops_reordered),
                ("changed", list(diff.stops_modified)),
            )
        }
    return ExplanationPayload(
        stops=stops,
        total_stops=len(plan.stops),
        total_wait_minutes=_minutes(plan.total_wait_minutes),
        total_walking_minutes=_minutes(plan.total_walking_minutes),
        must_do_in_plan=[names.get(n, n) for n in must_do if n in planned],
        unmet_must_do=[names.get(n, n) for n in plan.unmet_must_do],
        preferences=preferences,
        data={
            "park_data_read_at": _clock(provenance.retrieved_at),
            "sources": [source.value for source in provenance.data_sources],
            "wait_forecast": provenance.forecast_strategy,
            "planner": provenance.optimizer_strategy,
        },
        reason=reason,
        changes=changes,
    )


def _source(objective: GroupObjective, weight: str) -> str:
    source = objective.weight_provenance.get(weight)
    return source.value if source is not None else "default"


def _catalog_facts(deps_factory: DepsFactory) -> tuple[dict[str, str], dict[str, int]]:
    with open_deps(deps_factory) as deps:
        catalog = load_catalog(deps)
    names = {a.node_id: a.name for a in catalog}
    limits = {
        a.node_id: a.height_restriction_cm for a in catalog if a.height_restriction_cm is not None
    }
    return names, limits


class ExplainPlanUseCase:
    def __init__(self, deps_factory: DepsFactory = default_planning_deps) -> None:
        self._deps_factory = deps_factory

    def prepare(
        self,
        plan: Plan,
        check_result: CheckResult,
        *,
        guests: Sequence[Guest] = (),
        must_do: Sequence[str] = (),
        objective: GroupObjective | None = None,
        reason: str | None = None,
        diff: PlanDiff | None = None,
        lang: Language = DEFAULT_LANGUAGE,
    ) -> PreparedExplanation:
        """The payload the LLM explains from, and the template that stands in for it.

        A replan passes its ``reason`` and ``diff``; both halves then lead with them.
        """
        _require_valid(check_result)
        names, limits = _catalog_facts(self._deps_factory)
        payload = explanation_payload(
            plan,
            check_result,
            names,
            guests=guests,
            height_limits=limits,
            must_do=must_do,
            objective=objective,
            reason=reason,
            diff=diff,
        )
        template = (
            explain_replan(
                plan,
                check_result,
                names,
                reason=reason or "",
                diff=diff,
                guests=guests,
                height_limits=limits,
                lang=lang,
            )
            if diff is not None
            else explain_plan(
                plan,
                check_result,
                names,
                guests=guests,
                height_limits=limits,
                lang=lang,
            )
        )
        return PreparedExplanation(payload, template)
