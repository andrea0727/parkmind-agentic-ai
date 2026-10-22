# Testing

ParkMind is tested in five layers on top of the unit, integration and architecture tests. Every layer is a folder under `tests/`, and a pytest marker is applied from the folder (`tests/conftest.py`), so a layer can be selected without decorating each test.

| Layer | Folder | Marker | Needs | What it proves |
|---|---|---|---|---|
| 1. E2E | `tests/e2e` | `e2e`, `live` | Anthropic key, Postgres, internet | Whole guest journeys on real ports behave as the catalog expects |
| 2. Agent and workflow | `tests/workflow` | `workflow` | nothing | Routing, state, interrupts and retries of the graphs, replaying the catalog offline |
| 3. LLM evaluation | `tests/evals` | `llm_eval` | nothing; `live` tests need an Anthropic key | Accuracy, groundedness, consistency, tool-call correctness and instruction following, scored with DeepEval |
| 4. Observability | `tests/observability` | `observability` | nothing | Traces, spans, errors and token usage reach Langfuse with personal data masked |
| 5. Reliability, security, performance | `tests/resilience`, `tests/security`, `tests/performance` | `reliability`, `security`, `performance` | nothing | Failure recovery, prompt-injection and data-leak resistance, latency and cost budgets |

Everything except the `live` tests runs offline on in-memory ports and scripted model stand-ins, with no network, database or API key.

## Run

```bash
poetry run pytest                          # everything that needs no credentials
poetry run pytest -m workflow              # one layer
poetry run pytest -m "not live"            # explicit form of the default
poetry run pytest --live                   # also the live tests (or PARKMIND_RUN_LIVE=1)
poetry run pytest -m "security or reliability"
```

Live tests load credentials from the local `.env` into the process only; nothing is printed or written to a report.

## One catalog, five layers

`tests/e2e/catalog` holds the 36 user journeys. Each scenario states what it must prove at every layer:

- **E2E**: the scenario's `expected` behavior, judged on a live run.
- **Workflow**: the graph path (stages that ran, interrupts raised, what became active), replayed offline when the scenario has an `offline` spec (`tests/workflow/test_scenarios.py`).
- **LLM evaluation**: what the model output must get right (extraction, explanation).
- **Observability**: what the trace of the run must show.
- **Business rules**: the domain rule behind it (height limits, adult supervision, lunch window, party size).

The scenario x layer table and the criteria per scenario are in [SCENARIO_MATRIX.md](SCENARIO_MATRIX.md). The file is generated and a test fails when it is stale:

```bash
PYTHONPATH=src:tests poetry run python -m e2e.cli matrix
```

Judges are pure functions over the facts a run recorded, so the same judge decides a live run, an offline replay and a stored capture.

## Adding a scenario

1. Add a `Scenario` to the module of its area in `tests/e2e/catalog`, with a unique id and the next number.
2. Write the criteria for each layer it covers, or leave a layer empty when it does not.
3. Give it an `offline` extraction if the workflow layer can replay it without the LLM.
4. Regenerate the matrix and run `pytest tests/e2e tests/workflow`.

## Known issues the tests expose

- A replan that failed on an outage is not retried when the same event is sent again: the event is committed first and then dropped as a duplicate (`tests/resilience`, strict xfail).
- `height_cm` is not masked in Langfuse traces.

Related: [e2e reproduction](../../tests/e2e/README.md), [evaluation methodology](../evaluation/README.md), per-layer READMEs in `tests/evals`, `tests/observability` and `tests/resilience`.
