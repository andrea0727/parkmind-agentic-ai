"""Pass marks for the LLM evaluation layer, each with the reason it sits where it does."""

ACCURACY = 1.0
"""Every labeled hard constraint must survive extraction: one wrong height or dropped must-do breaks a guarantee."""

GROUNDEDNESS = 1.0
"""An explanation may not contain a single figure or attraction the plan does not; the product retries or falls back on one miss."""

CONSISTENCY = 1.0
"""Hard constraints must be identical on every repeated run: a flaky safety input is a defect, not noise."""

TOOL_CORRECTNESS = 1.0
"""Exactly the extraction tool, once, with nothing extra: the forced tool call has no partial credit."""

CONVERSATION_FLOW = 1.0
"""A question asked twice in the same words, or the guests' sentence read back to them, is the robotic behaviour this check exists to catch."""

JUDGED = 0.7
"""Judge scores are noisy and graded 0 to 1; 0.7 asks for clearly good, not perfect, and tolerates one rubric step of judge variance."""

DEFAULT_RUNS = 3
"""Repetitions of the same extraction: enough to expose a flip at temperature 0 without a large live bill (env PARKMIND_EVAL_RUNS)."""

CONVERSATION_RUNS = 5
"""Live conversations per dialogue and arm: one judged run is an anecdote, five give a pass rate (env PARKMIND_CONVERSATION_RUNS)."""

PASS_RATE = 0.8
"""Share of runs that must pass: a model reply is not deterministic, so the claim is "at least four in five", not "always"."""
