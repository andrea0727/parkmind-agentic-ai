from datetime import datetime, timedelta, timezone

from parkmind.core.contracts import (
    PartyConstraints,
    Plan,
    PreferenceSource,
    Provenance,
    Stop,
    StopKind,
)
from parkmind.core.contracts.enums import AttributionType
from parkmind.services.explanation.attribution_mapper import AttributionMapper
from parkmind.services.explanation.rationale_builder import RationaleBuilder


def test_attribution_mapper_stated_must_do() -> None:
    mapper = AttributionMapper()
    guest_constraints = PartyConstraints(
        party_size=1,
        guests=[],
        must_do=["space_mountain"],
        departure_time=datetime.now(timezone.utc),
        constraints_version=1,
    )

    attr, reason = mapper.attribute_stop("space_mountain", constraints=guest_constraints)
    assert attr == AttributionType.STATED
    assert "Must-Do" in reason


def test_attribution_mapper_learned_preference() -> None:
    mapper = AttributionMapper()
    weight_sources = {"pirates": PreferenceSource.LEARNED}

    attr, reason = mapper.attribute_stop("pirates", weight_sources=weight_sources)
    assert attr == AttributionType.LEARNED
    assert "inferred group affinity" in reason


def test_rationale_builder_generates_all_stops() -> None:
    tz = timezone.utc
    now = datetime(2026, 10, 11, 9, 0, tzinfo=tz)

    stops = [
        Stop(
            node_id="attr_a",
            kind=StopKind.ATTRACTION,
            arrival_time=now,
            departure_time=now + timedelta(minutes=20),
            expected_wait_minutes=10.0,
            walking_minutes=5.0,
            utility=10.0,
            served_guests=["g1"],
        )
    ]
    plan = Plan(
        plan_id="p1",
        version=1,
        stops=stops,
        total_wait_minutes=10.0,
        total_walking_minutes=5.0,
        objective_value=10.0,
        per_guest_satisfaction={"g1": 1.0},
        unmet_must_do=[],
        provenance=Provenance(
            snapshot_id="s1",
            retrieved_at=now,
            forecast_strategy="none",
            optimizer_strategy="greedy_insertion",
            constraints_version=1,
            objective_version="auto",
            preference_model_version="auto",
        ),
    )

    builder = RationaleBuilder()
    rationales = builder.build_rationales(plan)

    assert len(rationales) == 1
    assert rationales[0].node_id == "attr_a"
    assert rationales[0].wait_minutes == 10.0
    assert rationales[0].walk_minutes == 5.0