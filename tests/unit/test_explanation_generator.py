from datetime import datetime, timedelta, timezone

from parkmind.core.contracts import (
    ExplainRequest,
    Plan,
    Provenance,
    Stop,
    StopKind,
)
from parkmind.services.explanation.generator import PlanExplanationGenerator
from parkmind.services.explanation.grounding_validator import (
    GroundingContext,
    NumericalGroundingValidator,
)


def test_generator_successful_grounding() -> None:
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

    req = ExplainRequest(plan=plan, catalog_names={"attr_a": "Roller Coaster A"})
    generator = PlanExplanationGenerator()

    explanation = generator.generate(req)

    assert explanation.grounding_verified is True
    assert explanation.fallback_used is False
    assert "Roller Coaster A" in explanation.full_text
    assert "15" in explanation.full_text


class MockMaliciousValidator(NumericalGroundingValidator):
    """Mock validator simulating an LLM hallucination catching failure."""

    def validate(
        self, text: str, context: GroundingContext
    ) -> tuple[bool, list[str]]:
        # Always fail validation and report a hallucinated number
        return False, ["num:999"]


def test_generator_triggers_fallback_on_invalid_grounding() -> None:
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

    req = ExplainRequest(plan=plan)
    # Inject the malicious validator that always fails
    generator = PlanExplanationGenerator(validator=MockMaliciousValidator())

    explanation = generator.generate(req)

    assert explanation.grounding_verified is True
    assert explanation.fallback_used is True
    assert "Fallback Summary" in explanation.full_text