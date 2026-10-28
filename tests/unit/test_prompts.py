"""Every LLM prompt is pinned to its version.

``Provenance.prompt_versions`` says which prompt read the guests and which one wrote
the explanation (G7). That record is only true if a prompt's text cannot change
under the same version, so each version keeps a fingerprint of its exact text.
"""

import hashlib

import pytest

from parkmind.agents.ask_prompt import ASK_PROMPT_VERSION, ASK_SYSTEM_PROMPT
from parkmind.agents.elicit_prompt import ELICIT_PROMPT_VERSION, ELICIT_SYSTEM_PROMPT
from parkmind.agents.explain_prompt import (
    EXPLAIN_PROMPT_VERSION,
    EXPLAIN_SYSTEM_PROMPT,
)

# version -> first 12 hex digits of the sha256 of the prompt's text.
# Changed a prompt? Bump its *_PROMPT_VERSION and add the new pair here.
FINGERPRINTS = {
    "elicit-v9": "3d2f3399ba34",
    "explain-v1": "c43e0322ddda",
    "ask-v13": "b5e7369d2084",
}

PROMPTS = [
    pytest.param(ELICIT_PROMPT_VERSION, ELICIT_SYSTEM_PROMPT, id="elicit"),
    pytest.param(EXPLAIN_PROMPT_VERSION, EXPLAIN_SYSTEM_PROMPT, id="explain"),
    pytest.param(ASK_PROMPT_VERSION, ASK_SYSTEM_PROMPT, id="ask"),
]


def fingerprint(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


@pytest.mark.parametrize(("version", "prompt"), PROMPTS)
def test_a_prompt_cannot_change_without_a_new_version(
    version: str, prompt: str
) -> None:
    assert version in FINGERPRINTS, (
        f"{version} has no recorded fingerprint: add {version!r}: "
        f"{fingerprint(prompt)!r} to FINGERPRINTS"
    )
    assert fingerprint(prompt) == FINGERPRINTS[version], (
        f"the text of {version} changed: bump its version and record "
        f"{fingerprint(prompt)!r} for the new one"
    )


def test_every_version_is_distinct() -> None:
    versions = [p.values[0] for p in PROMPTS]
    assert len(set(versions)) == len(versions)
