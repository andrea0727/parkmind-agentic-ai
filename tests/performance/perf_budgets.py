"""Budgets for the offline performance tests, each with why it is that loose."""

import os

BUDGET_SCALE_ENV = "PARKMIND_PERF_BUDGET_SCALE"
RUNS_ENV = "PARKMIND_PERF_RUNS"


def _scale() -> float:
    try:
        return max(0.1, float(os.getenv(BUDGET_SCALE_ENV, "1")))
    except ValueError:
        return 1.0


SCALE = _scale()
"""Multiplies every time budget; raise it on a slow machine instead of editing budgets."""

JOURNEY_RUNS = max(3, int(os.getenv(RUNS_ENV, "20")))
"""Repetitions of the full journey; 20 gives a stable p95 in a few seconds."""

JOURNEY_P50_SECONDS = 0.75 * SCALE
"""Typical message -> confirm -> approve; the offline journey takes about 10 ms, so this is wide slack."""
JOURNEY_P95_SECONDS = 2.0 * SCALE
"""A guest never waits seconds on in-memory work; this only trips on an accidental O(n^2) or sleep."""

CONCURRENT_THREADS = 30
"""Distinct sessions run at once on one graph: inside the 20-50 range of one API worker's load."""
CONCURRENT_TOTAL_SECONDS = 20.0 * SCALE
"""Python threads share the GIL, so 30 journeys cost about 30 serial ones; 20 s leaves wide slack."""

EXTRACTOR_CALLS_HAPPY_PATH = 1
"""One extraction reads the whole request; a second call would double the cost of every plan."""
NARRATOR_CALLS_HAPPY_PATH = 1
"""One grounded explanation per proposal."""
NARRATOR_CALLS_REPAIR_PATH = 2
"""An ungrounded draft gets one retry, then the template (section 40)."""
EXTRACTOR_CALLS_REPAIR_PATH = 2
"""A schema failure gets one repair attempt, then the guest is asked."""

NARRATOR_PAYLOAD_TOKENS_TYPICAL = 1_200
"""A typical plan's payload is about 500 tokens (chars / 4); twice that flags unbounded growth."""
NARRATOR_PAYLOAD_TOKENS_MUST_DO_12 = 2_200
"""Twelve must-dos make a plan of about 1,000 tokens; twice that flags per-stop bloat."""
NARRATOR_PAYLOAD_TOKENS_PARTY_CAP = 1_200
"""The payload names no guests, so a 30-guest party costs the same as a pair; growth means guests leaked in."""

PLANNER_MUST_DO_COUNTS = (1, 6, 12)
"""One request, a busy day, and the heaviest request a guest group realistically makes."""
PLANNER_BUILD_SECONDS = 2.0 * SCALE
"""Greedy insertion over a few dozen nodes is milliseconds; 2 s catches only a blow-up."""
PLANNER_CHECK_SECONDS = 1.0 * SCALE
"""The checker walks the plan once per rule; 1 s catches only a blow-up."""


def tokens(text: str) -> int:
    """The rough token count used by the cost tests: one token per four characters."""
    return len(text) // 4
