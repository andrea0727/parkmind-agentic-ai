"""Live EXPLAIN: how often the real model labels preference weights as the prompt asks.

Skipped without ANTHROPIC_API_KEY (like the live elicit eval). Every miss in the
stated/learned label check costs a retry and, after two, the deterministic template,
so the rate matters. Run with ``-s`` to see it.
"""

import os
from datetime import timedelta

import factories
import pytest

from parkmind.agents.explain_agent import AnthropicNarrator, explain_grounded
from parkmind.agents.explain_schema import ExplanationDraft
from parkmind.agents.grounding import mislabelled_preferences, ungrounded_numerals
from parkmind.core.contracts import (
    CheckResult,
    EventThresholds,
    FairnessConfig,
    GroupObjective,
    HardConstraintSet,
    PreferenceSource,
)
from parkmind.services.use_cases.explain_plan import (
    PreparedExplanation,
    explain_plan,
    explanation_payload,
)

RUNS = 5
MIN_FIRST_ATTEMPT_RATE = 0.6

NAMES = {"a1": "Space Mountain", "a2": "Big Thunder Mountain"}


def _prepared() -> PreparedExplanation:
    start = factories.stop().arrival_time
    plan = factories.plan(
        stops=[
            factories.stop(
                node_id="a1",
                arrival_time=start,
                departure_time=start + timedelta(minutes=47),
                expected_wait_minutes=27,
            ),
            factories.stop(
                node_id="a2",
                arrival_time=start + timedelta(minutes=52),
                departure_time=start + timedelta(minutes=90),
                expected_wait_minutes=13,
            ),
        ],
        total_wait_minutes=40,
        total_walking_minutes=8,
    )
    objective = GroupObjective(
        objective_version="t",
        weights={"affinity:thrill": 0.8, "lambda_queue": 0.33, "lambda_walk": 0.5},
        weight_provenance={
            "affinity:thrill": PreferenceSource.STATED,
            "lambda_queue": PreferenceSource.LEARNED,
        },
        hard_constraints=HardConstraintSet(),
        fairness=FairnessConfig(lambda_fairness=0.3, min_satisfaction_floor=0.0),
        event_thresholds=EventThresholds(),
    )
    check = CheckResult(valid=True)
    return PreparedExplanation(
        explanation_payload(plan, check, NAMES, must_do=["a1"], objective=objective),
        explain_plan(plan, check, NAMES),
    )


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY"
)
def test_live_model_labels_stated_and_learned_weights_as_asked() -> None:
    prepared = _prepared()
    payload_json = prepared.payload.to_json()
    narrator = AnthropicNarrator()
    labelled = {"0.8": "stated", "0.33": "learned", "0.5": "default"}
    clean = 0
    for _ in range(RUNS):
        draft = ExplanationDraft.model_validate(narrator.narrate(payload_json))
        grounded = not ungrounded_numerals(draft.text, payload_json)
        labels = mislabelled_preferences(draft.text, labelled)
        print(f"explain live: grounded={grounded} label_errors={labels}")
        clean += grounded and not labels

    rate = clean / RUNS
    print(f"explain live: {clean}/{RUNS} first answers pass both checks")
    final = explain_grounded(narrator, prepared)
    print(
        f"explain live: explain_grounded -> {final.source} in {final.attempts} call(s)"
    )

    assert rate >= MIN_FIRST_ATTEMPT_RATE, f"{clean}/{RUNS} first answers were clean"
