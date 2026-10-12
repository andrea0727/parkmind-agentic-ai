from datetime import datetime, timedelta, timezone

from parkmind.core.contracts import Plan, Provenance, Stop, StopKind
from parkmind.services.explanation.grounding_validator import (
    GroundingContext,
    NumericalGroundingValidator,
)


def test_grounding_validator_passes_valid_numbers() -> None:
    tz = timezone.utc
    now = datetime(2026, 10, 11, 9, 0, tzinfo=tz)
    
    stops = [
        Stop(
            node_id="attr_a",
            kind=StopKind.ATTRACTION,
            arrival_time=now,
            departure_time=now + timedelta(minutes=30),
            expected_wait_minutes=15.0,
            walking_minutes=5.0,
            utility=10.0,
            served_guests=["g1"],
        )
    ]
    plan = Plan(
        plan_id="p1",
        version=1,
        stops=stops,
        total_wait_minutes=15.0,
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

    ctx = GroundingContext.from_inputs(plan)
    validator = NumericalGroundingValidator()

    # Text containing exact ground truth values
    valid_text = "Your plan has 1 stops at 9:00 with a wait of 15 minutes and 5 walking."
    is_valid, ungrounded = validator.validate(valid_text, ctx)
    assert is_valid is True
    assert ungrounded == []


def test_grounding_validator_rejects_hallucinated_numbers() -> None:
    tz = timezone.utc
    now = datetime(2026, 10, 11, 9, 0, tzinfo=tz)
    
    stops = [
        Stop(
            node_id="attr_a",
            kind=StopKind.ATTRACTION,
            arrival_time=now,
            departure_time=now + timedelta(minutes=30),
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

    ctx = GroundingContext.from_inputs(plan)
    validator = NumericalGroundingValidator()

    # "25" is a hallucinated wait time not present in the plan (actual wait is 10)
    hallucinated_text = "You will wait 25 minutes at 9:00."
    is_valid, ungrounded = validator.validate(hallucinated_text, ctx)
    assert is_valid is False
    assert "num:25" in ungrounded