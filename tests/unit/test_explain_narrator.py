"""The Claude narrator of EXPLAIN (P0-31): no key, no call."""

import pytest

from parkmind.agents.explain_agent import AnthropicNarrator, NarratorUnavailableError
from parkmind.agents.explain_prompt import EXPLAIN_PROMPT_VERSION, EXPLAIN_SYSTEM_PROMPT


def test_without_an_api_key_nothing_is_sent():
    with pytest.raises(NarratorUnavailableError):
        AnthropicNarrator(api_key="").narrate('{"stops": []}')


def test_the_prompt_is_versioned_and_states_the_grounding_and_labelling_rules():
    assert EXPLAIN_PROMPT_VERSION == "explain-v1"
    for rule in ("exactly as given", "(stated)", "(learned)", "unmet_must_do"):
        assert rule in EXPLAIN_SYSTEM_PROMPT, rule
