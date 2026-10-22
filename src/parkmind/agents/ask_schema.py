"""Structured output of the ``ask`` step: one field, the question for the guests."""

from pydantic import BaseModel, ConfigDict, Field

MAX_QUESTION_CHARS = 600


class QuestionDraft(BaseModel):
    """The next message for the guests, worded from what is still missing."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description=(
            "The message for the guests: a short reply to what they just said, then "
            "the question. Every number and clock time comes from `must_ask` or the "
            "guests' messages."
        ),
    )
