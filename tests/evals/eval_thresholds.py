"""Pass marks for the LLM evaluation layer, each with the reason it sits where it does."""

ACCURACY = 1.0
"""Every labeled hard constraint must survive extraction: one wrong height or dropped must-do breaks a guarantee."""

GROUNDEDNESS = 1.0
"""An explanation may not contain a single figure or attraction the plan does not; the product retries or falls back on one miss."""

CONSISTENCY = 1.0
"""Hard constraints must be identical on every repeated run: a flaky safety input is a defect, not noise."""

TOOL_CORRECTNESS = 1.0
"""Exactly the extraction tool, once, with nothing extra: the forced tool call has no partial credit."""

JUDGED = 0.7
"""Judge scores are noisy and graded 0 to 1; 0.7 asks for clearly good, not perfect, and tolerates one rubric step of judge variance."""

DEFAULT_RUNS = 3
"""Repetitions of the same extraction: enough to expose a flip at temperature 0 without a large live bill (env PARKMIND_EVAL_RUNS)."""
