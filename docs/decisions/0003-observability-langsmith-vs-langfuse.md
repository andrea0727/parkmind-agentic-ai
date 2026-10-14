# ADR 0003 — Observability: Langfuse for traces, LangSmith off by default

- **Status:** Proposed (2026-10-14)
- **Backlog:** issue #5 (evaluate LangSmith); informs P1-18 (Langfuse observability)
- **Architecture:** §42 (Observability), §40 (groundedness score), §45 (metrics), C19 (raw user turns masked in traces)

## Context

§42 names Langfuse as the tool that records graph runs, LLM calls, tool selection, MCP calls, the planner, the checker and proposals. It attaches the provenance metadata (`thread_id`, `plan_id`, `snapshot_id`, prompt versions…) and the scores (constraint violations, groundedness, latency, cost…). P1-18 implements it. Issue #5 asks whether LangSmith, LangChain's own platform, should play that role, or part of it, for tracing, debugging, monitoring and evaluation.

Three facts about the code today shape the decision:

1. **No observability is wired.** No callback or tracer is configured anywhere in `src/`. `langfuse` is declared (`pyproject.toml`, `>=2.0`; the lock pins 4.15.3, whose API is not v2's) but unused. `langsmith` 0.12.5 comes in only as a dependency of `langchain-core`.
2. **LangSmith traces with no code.** When `LANGSMITH_TRACING=true` (or the legacy `LANGCHAIN_TRACING_V2=true`) and an API key are in the environment, `langchain-core` adds a `LangChainTracer` to every run by itself (`langchain_core/tracers/context.py`). The repository loads `.env` from `config/settings.py`, the e2e harness and `langgraph.json`. So **two lines in `.env` would send every graph run to LangSmith Cloud, raw guest messages included**, with nothing masking them:
   - the raw turns are in `ParkMindState.messages`, recorded as node inputs and as the final state;
   - they are also copied into the elicit prompt (`"Guest messages, oldest first: …"`).

   That breaks C19, and the data is about minors' accessibility and fatigue.
3. **C19 masking must be selective.** The baseline wants typed outputs traced verbatim and raw user turns masked. Hiding all inputs or outputs is not compliant either: it loses the typed outputs. Whichever tool we use, the masking is our code and needs a test.

## Options

Sources: official documentation and pricing pages, read 2026-10-13. Prices change; recheck them before relying on them.

| Criterion | LangSmith | Langfuse |
|---|---|---|
| LangGraph tracing | Automatic from env vars; every node and LLM call. `@traceable` for our own code. Threads view keyed by `thread_id` (LangGraph copies it to metadata). | `CallbackHandler` (`langfuse.langchain`) passed in `config["callbacks"]`; `@observe` for our own code; `session_id` / `user_id` via `propagate_attributes`. SDK v4 (v3 endpoints deprecated on Cloud from 2026-11-16). |
| C19 masking | `Client(hide_inputs=fn, hide_outputs=fn)`, `create_anonymizer` (regex), `@traceable(process_inputs=…)`. `LANGSMITH_HIDE_INPUTS` alone isn't enough: the final state still carries `messages`. The anonymizer is skipped when a HIDE env var is set. | `Langfuse(mask=fn)` (legacy) or `mask_otel_spans=fn` (recommended). There is no documented example for `CallbackHandler` traces, so it has to be proven with a test. Server-side masking is Enterprise only. |
| Where the data lives | LangChain Cloud, US (default) or EU. Self-hosting needs an Enterprise license (Kubernetes / Helm). | Self-hosted with Docker Compose, MIT license, free; or Cloud US / EU / JP. |
| Free tier | Developer: 1 seat, 5k base traces/month, 14-day retention. | Self-hosted: unlimited. Cloud Hobby: 2 users, 50k units/month (a graph run is many units), 30 days. |
| Evaluation | Datasets, experiments, code and LLM-as-judge evaluators, annotation queues, online evaluators. **pytest integration** (`@pytest.mark.langsmith`), which uploads test inputs and outputs by default. | Scores, datasets, experiments, LLM-as-judge, annotation queues, a GitHub Action for regressions. No pytest plugin. |
| Development | **LangGraph Studio** (`langgraph dev`). The repo already has `langgraph.json`; `langgraph-cli` isn't installed. With `LANGSMITH_TRACING=false`, "no data leaves your local server". | Trace UI only. |
| Team (3 people) | 1 free seat; Plus is $39/seat/month. | Self-hosted: no seat limit. |

## Decision

1. **Production and demo traces: Langfuse, self-hosted** (§42 and P1-18 stand).
   - It keeps guest data, including minors' accessibility- and fatigue-adjacent text, on infrastructure the project controls, at no cost and for the whole team.
   - With LangSmith, on any plan we could use, the data would go to a third-party cloud.
2. **LangSmith is optional for local development only, never with real guest data.**
   - Studio (`langgraph dev`) works with tracing off.
   - LangSmith's pytest/evaluation tooling may be used for **offline** evaluation on fixture scenarios only (`tests/fixtures/elicit/scenarios.json` and the e2e scenarios). It is opt-in per developer.
3. **Tracing is refused until masking exists.** Until P1-18 wires a tested C19 masking hook, the application refuses to start or build a graph when LangSmith or Langfuse tracing is switched on through the environment (`config.tracing_guard`). `.env.example` sets `LANGSMITH_TRACING=false` with a comment.
4. **P1-18 must land with one redaction function and its test:**
   - the function drops `HumanMessage` content and the elicit prompt's guest block, and keeps the typed outputs (`PartyConstraints`, `Event`, plan ids, check results);
   - it is installed through `Langfuse(mask=… / mask_otel_spans=…)`;
   - a canary test runs a graph with a unique string in a guest turn and asserts it never reaches the exporter.

   The same function can back LangSmith's `hide_inputs` / `hide_outputs` if a developer turns LangSmith on.
5. **Housekeeping for P1-18:** raise the `langfuse` floor to `^4` (the lock already pins 4.15.3); `>=2.0` suggests an API the code would not use.

## Consequences

- **Gains:**
  - No accidental leak is possible today: the guard fails closed.
  - The baseline's choice stands, now justified on C19, cost and team size.
  - Studio and LangSmith evaluation stay available for local work on fixtures.
- **Costs:**
  - Self-hosting Langfuse is one more service to run (Docker Compose next to Postgres).
  - We give up LangSmith's zero-code tracing and its hosted evaluators for production traffic.
  - The masking is ours to write and test either way.
- **Revisit if:**
  - the project moves to a LangSmith Enterprise / self-hosted deployment;
  - Langfuse's `CallbackHandler` masking proves unreliable in the P1-18 canary test.

## References

- LangSmith: [trace with LangGraph](https://docs.langchain.com/langsmith/trace-with-langgraph), [mask inputs/outputs](https://docs.langchain.com/langsmith/mask-inputs-outputs), [threads](https://docs.langchain.com/langsmith/threads), [pytest](https://docs.langchain.com/langsmith/pytest), [self-hosted](https://docs.langchain.com/langsmith/self-hosted), [Studio](https://docs.langchain.com/langsmith/quick-start-studio), [pricing](https://www.langchain.com/pricing).
- Langfuse: [LangChain integration](https://langfuse.com/integrations/frameworks/langchain), [masking](https://langfuse.com/docs/observability/features/masking), [Python v3→v4](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4), [evaluation](https://langfuse.com/docs/evaluation/overview), [pricing](https://langfuse.com/pricing), [self-host pricing](https://langfuse.com/pricing-self-host).
- Guardrail inventory: ADR 0004.
