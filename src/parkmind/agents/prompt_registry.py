"""Every LLM capability of the Concierge, in one place (§7, ADR 0004).

One row per module that calls an LLM: its job, its system prompt and version, the one
output schema it is forced to fill, and the key its version is recorded under in
``Provenance.prompt_versions``. Tests read this table: a module that builds an LLM
client without a row here fails CI (``tests/unit/test_llm_agency.py``), and every row's
prompt must start with the Concierge core (``tests/unit/test_prompts.py``).

The agents do not import this module; it describes them. Interpret (P1-23) and the
agentic context loader (P1-22) add their row when they are built.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from parkmind.agents.ask_prompt import ASK_JOB, ASK_PROMPT_VERSION, ASK_SYSTEM_PROMPT
from parkmind.agents.ask_schema import QuestionDraft
from parkmind.agents.elicit_prompt import (
    ELICIT_JOB,
    ELICIT_PROMPT_VERSION,
    ELICIT_SYSTEM_PROMPT,
)
from parkmind.agents.elicit_schema import ElicitationExtraction
from parkmind.agents.explain_prompt import (
    EXPLAIN_JOB,
    EXPLAIN_PROMPT_VERSION,
    EXPLAIN_SYSTEM_PROMPT,
)
from parkmind.agents.explain_schema import ExplanationDraft


@dataclass(frozen=True)
class LlmCapability:
    name: str
    module: str
    """The module that calls the LLM, relative to ``src/parkmind``."""
    job: str
    system_prompt: str
    prompt_version: str
    output_schema: type[BaseModel]
    provenance_key: str
    """Its key in ``Provenance.prompt_versions``."""
    fallback: str
    """What the guests get when the model cannot answer."""


CAPABILITIES: tuple[LlmCapability, ...] = (
    LlmCapability(
        name="elicit",
        module="agents/elicit_agent.py",
        job=ELICIT_JOB,
        system_prompt=ELICIT_SYSTEM_PROMPT,
        prompt_version=ELICIT_PROMPT_VERSION,
        output_schema=ElicitationExtraction,
        provenance_key="elicit",
        fallback="one repair retry, then the guests are asked to rephrase",
    ),
    LlmCapability(
        name="ask",
        module="agents/ask_agent.py",
        job=ASK_JOB,
        system_prompt=ASK_SYSTEM_PROMPT,
        prompt_version=ASK_PROMPT_VERSION,
        output_schema=QuestionDraft,
        provenance_key="ask",
        fallback="the fixed question",
    ),
    LlmCapability(
        name="explain",
        module="agents/explain_agent.py",
        job=EXPLAIN_JOB,
        system_prompt=EXPLAIN_SYSTEM_PROMPT,
        prompt_version=EXPLAIN_PROMPT_VERSION,
        output_schema=ExplanationDraft,
        provenance_key="explain",
        fallback="one grounding retry, then the template",
    ),
)

BY_NAME = {capability.name: capability for capability in CAPABILITIES}
