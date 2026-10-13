"""Turns planner results into the API's JSON shapes. Names come from the catalog; nothing is decided here."""

from collections.abc import Mapping
from typing import Any

from parkmind.api.schemas import (
    ChangedEntry,
    ConfirmationOut,
    DiffEntry,
    DiffOut,
    HeldEventOut,
    MetricsOut,
    PlanOut,
    ProposalOut,
    ProvenanceOut,
    StopOut,
    TurnOut,
)
from parkmind.core.contracts import Attraction, Plan, Proposal, Stop, StopKind
from parkmind.core.conversation import Turn
from parkmind.services.use_cases.describe_plan import (
    DescribePlanUseCase,
    PlanDescription,
)

_NON_ATTRACTION_TITLES = {StopKind.MEAL: "Meal break", StopKind.REST: "Rest"}


def stop_id(stop: Stop) -> str:
    return (
        stop.node_id
        if stop.kind == StopKind.ATTRACTION
        else f"{stop.node_id}:{stop.kind.value}"
    )


def _title(node_id: str, kind: StopKind, attractions: Mapping[str, Attraction]) -> str:
    if kind in _NON_ATTRACTION_TITLES:
        return _NON_ATTRACTION_TITLES[kind]
    attraction = attractions.get(node_id)
    return attraction.name if attraction else node_id


def _title_of_label(label: str, attractions: Mapping[str, Attraction]) -> str:
    base = label.split("#", 1)[0]
    node_id, _, kind = base.partition(":")
    return _title(node_id, StopKind(kind) if kind else StopKind.ATTRACTION, attractions)


def present_plan(plan: Plan, attractions: Mapping[str, Attraction]) -> PlanOut:
    return PlanOut(
        plan_id=plan.plan_id,
        version=plan.version,
        stops=[
            StopOut(
                stop_id=stop_id(s),
                node_id=s.node_id,
                kind=s.kind.value,
                title=_title(s.node_id, s.kind, attractions),
                land=attractions[s.node_id].land if s.node_id in attractions else None,
                arrival=s.arrival_time,
                departure=s.departure_time,
                wait_minutes=s.expected_wait_minutes,
                walking_minutes=s.walking_minutes,
                served_guests=list(s.served_guests),
            )
            for s in plan.stops
        ],
        total_wait_minutes=plan.total_wait_minutes,
        total_walking_minutes=plan.total_walking_minutes,
        unmet_must_do=list(plan.unmet_must_do),
        snapshot_id=plan.provenance.snapshot_id,
        retrieved_at=plan.provenance.retrieved_at,
    )


def _entries(
    labels: list[str], attractions: Mapping[str, Attraction]
) -> list[DiffEntry]:
    return [
        DiffEntry(stop_id=lb, title=_title_of_label(lb, attractions)) for lb in labels
    ]


def present_proposal(proposal: Proposal, described: PlanDescription) -> ProposalOut:
    names = described.attractions
    diff = proposal.diff
    base = described.base_plan
    modified: dict[str, Any] = diff.stops_modified
    return ProposalOut(
        proposal_id=proposal.proposal_id,
        base_plan_id=proposal.base_plan_id,
        candidate_plan_id=proposal.candidate_plan_id,
        trigger=proposal.reason,
        explanation=proposal.explanation,
        diff=DiffOut(
            removed=_entries(diff.stops_removed, names),
            added=_entries(diff.stops_added, names),
            moved=_entries(diff.stops_reordered, names),
            modified=[
                ChangedEntry(
                    stop_id=lb, title=_title_of_label(lb, names), changes=changes
                )
                for lb, changes in modified.items()
            ],
        ),
        metrics=MetricsOut(
            wait_minutes_before=base.total_wait_minutes if base else None,
            wait_minutes_after=described.plan.total_wait_minutes,
            walking_minutes_before=base.total_walking_minutes if base else None,
            walking_minutes_after=described.plan.total_walking_minutes,
        ),
        provenance=ProvenanceOut(
            snapshot_id=proposal.provenance.snapshot_id,
            retrieved_at=proposal.provenance.retrieved_at,
            forecast_strategy=proposal.provenance.forecast_strategy,
            optimizer_strategy=proposal.provenance.optimizer_strategy,
            constraints_version=proposal.provenance.constraints_version,
        ),
        plan=present_plan(described.plan, names),
    )


class Presenter:
    def __init__(self, describe: DescribePlanUseCase) -> None:
        self._describe = describe

    def plan(self, plan: Plan) -> PlanOut:
        return present_plan(plan, self._describe.execute(plan).attractions)

    def proposal(self, proposal: Proposal, candidate: Plan) -> ProposalOut:
        described = self._describe.execute(candidate, proposal.base_plan_id)
        return present_proposal(proposal, described)

    def turn(self, turn: Turn) -> TurnOut:
        confirmation = turn.confirmation
        return TurnOut(
            thread_id=turn.thread_id,
            status=turn.status,
            messages=list(turn.messages),
            missing=list(turn.missing),
            question=turn.question,
            confirmation=None
            if confirmation is None
            else ConfirmationOut(
                pending=list(confirmation.pending),
                echo=list(confirmation.echo),
                accessibility_guests=list(confirmation.accessibility_guests),
                consent_required=confirmation.consent_required,
            ),
            proposal=None
            if turn.proposal is None or turn.candidate_plan is None
            else self.proposal(turn.proposal, turn.candidate_plan),
            held_events=[
                HeldEventOut(
                    event_id=e.event_id,
                    type=e.type,
                    attraction_id=e.attraction_id,
                    guest_id=e.guest_id,
                    confidence=e.confidence,
                )
                for e in turn.held_events
            ],
            error=turn.error,
        )
