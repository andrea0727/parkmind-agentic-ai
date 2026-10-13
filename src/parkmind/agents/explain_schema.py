"""Structured output of the ``explain`` step (P0-31).

The LLM returns one field: the explanation text. It is validated here, then
checked numeral by numeral against the payload (``agents/grounding.py``) before
it can reach the guests.
"""

from pydantic import BaseModel, ConfigDict, Field

MAX_EXPLANATION_CHARS = 3000


class ExplanationDraft(BaseModel):
    """The explanation of a validated plan, written only from the payload."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1,
        max_length=MAX_EXPLANATION_CHARS,
        description=(
            "The explanation for the guests. Every number and clock time must be "
            "copied from the payload; preference figures say (stated) or (learned)."
        ),
    )
