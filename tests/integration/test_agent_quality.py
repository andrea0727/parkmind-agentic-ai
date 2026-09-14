"""
Agent quality checks — the 4 checks scoped for this project (see the wiki,
"Quality & Testing" page, for the realistic-scope rationale).

We deliberately do NOT build the full QA pipeline (DeepEval/Ragas scoring,
garak/PyRIT adversarial/prompt-injection testing, CI regression suites).
With one QA-capable person on a 3-person team in a 1-month build, these
four checks are the representative set that catches the failure modes that
actually matter: an agent that invents facts, an agent that silently drops
a stated constraint, an agent that leaks one guest's data into another's
session, and an agent that ignores the guest model it's supposedly built
around.

Each is a stub — fill in the real assertion once the corresponding piece
of agents/ and graph/ exists. Keep them this simple; they're not meant to
grow into a framework.

Mapping, so it's explicit where each concern lives (nothing here needs a
new test file beyond what's already in this folder):

  - "Do the agents/nodes communicate correctly?" -> NOT a new test. That's
    exactly what test_initial_planning_graph.py and
    test_replanning_graph.py already check: does state flow correctly
    from one node to the next (constraints -> resolved_preferences ->
    candidate_plan -> proposal, etc).
  - "Do they save/keep memory correctly?" -> TestMemorySessionIsolation
    below.
  - "Is the LLM output grounded / not hallucinating?" -> TestHallucinationDetection
    below. DeepEval's FaithfulnessMetric is an optional one-call addition
    here (see the commented example) — not a framework to build.
  - "Does the plan actually reflect the guest?" -> TestGuestAlignment below.
"""

import pytest


class TestHallucinationDetection:
    """Groundedness: every fact in an explanation must trace back to the
    plan's Provenance. If the explanation states a wait time or walking
    time, that number must exist in the plan's data — never invented.

    MVP option: a plain substring/number match against the plan's data is
    enough for month 1. DeepEval is a drop-in upgrade, not a requirement —
    example below, commented out, to show it really is just one call:

        # from deepeval.metrics import FaithfulnessMetric
        # from deepeval.test_case import LLMTestCase
        # metric = FaithfulnessMetric(threshold=0.7)
        # case = LLMTestCase(
        #     input=user_message,
        #     actual_output=explanation,
        #     retrieval_context=[str(plan.model_dump())],
        # )
        # metric.measure(case)
        # assert metric.score >= 0.7

    Add `deepeval` to requirements.txt only if/when this gets used for
    real — it's not installed by default.
    """

    def test_explanation_only_cites_grounded_facts(self):
        pytest.skip("TODO: wire up once ConciergeAgent.explain() exists")


class TestBusinessRuleValidation:
    """The agent's constraint extraction must not silently invent or drop
    hard constraints. If the guest mentions a walking limit, the extracted
    output must contain it — regardless of whether the final plan happens
    to be fine anyway."""

    def test_stated_accessibility_constraint_is_extracted(self):
        pytest.skip("TODO: wire up once ConciergeAgent.extract_constraints() exists")


class TestMemorySessionIsolation:
    """Two different thread_ids must never see each other's state — the
    cheapest thing to get catastrophically wrong with LangGraph
    checkpointing."""

    def test_two_threads_do_not_share_state(self):
        pytest.skip("TODO: wire up once graph/initial_planning_graph.py exists")


class TestGuestAlignment:
    """Does the plan actually reflect the guest's stated preferences? The
    cheapest version of Personalization Lift, applied to one profile."""

    def test_low_intensity_preference_avoids_high_intensity_stops(self):
        pytest.skip("TODO: wire up once BuildPlanUseCase exists")