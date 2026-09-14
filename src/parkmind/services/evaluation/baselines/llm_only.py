"""
Baseline 3: a general-purpose LLM + tools, no guest model, no deterministic
optimizer, no constraint gate. The direct answer to "why not just use
ChatGPT?" (see docs/architecture — the wiki's "Why ParkMind and not
ChatGPT" section).

Month-1 scope: doesn't need to be an automated pipeline — running it
manually 2-3 times against the same scenario as ParkMind is enough evidence
for the demo narrative.
"""


def run_llm_only_baseline(user_message: str, park_context: dict) -> str:
    raise NotImplementedError
