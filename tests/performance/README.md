# Performance tests

Offline checks that a plan stays fast and cheap. They use in-memory ports and scripted fakes: no network, database or API key.

Run them with:

```
poetry run pytest tests/performance -q
```

| File | What it bounds |
|---|---|
| `test_latency_budget.py` | p50 and p95 of the full journey (message, confirmation, approval) over repeated runs on one graph |
| `test_concurrency.py` | 30 sessions at once on one graph and checkpointer: no errors, each session keeps its own state, total time |
| `test_llm_call_budget.py` | model calls per plan (one extraction, one explanation; repair paths capped at two) and the narrator payload size in tokens, including 12 must-dos and a party at the size cap |
| `test_planner_scalability.py` | optimizer and checker time with 1, 6 and 12 must-dos |

Every budget lives in `perf_budgets.py` with a one-line reason.

## Why the time budgets are loose

The journey takes milliseconds offline, so the time budgets sit one to two orders of magnitude above it. They exist to catch an accidental quadratic loop, a sleep or a lock held across the whole run, not to rank machines. A slow CI runner will not trip them. The call-count and token budgets are exact or about twice the measured size, because those do not depend on the machine.

## Settings

- `PARKMIND_PERF_RUNS`: journeys timed by the latency tests (default 20).
- `PARKMIND_PERF_BUDGET_SCALE`: multiplier on every time budget (default 1), for slow machines.

## Not covered

Real model latency, network time, Postgres and behaviour under multiple worker processes. The numbers here are the planner's own cost, not what a guest waits for.
