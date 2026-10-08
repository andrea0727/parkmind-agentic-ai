"""The plan's Provenance records which elicit prompt produced its constraints."""

import asyncio

import factories

from parkmind.agents.elicit_prompt import ELICIT_PROMPT_VERSION
from parkmind.agents.plan_synthesis_agent import synthesize_plan


def test_the_plan_provenance_records_the_elicit_prompt_version() -> None:
    state = {
        "thread_id": "t1",
        "messages": [],
        "constraints": factories.party_constraints(),
        "live_context": factories.live_context(),
    }

    plan = asyncio.run(synthesize_plan(state))["candidate_plan"]

    assert plan.provenance.prompt_versions["elicit"] == ELICIT_PROMPT_VERSION
