# Observability tests

Layer 4 of the test strategy: a real offline graph run is traced through the production
Langfuse path (`langfuse_callbacks`, the masked handler, `mask_for_trace`), and the spans
that would be sent to Langfuse are read from an in-memory OpenTelemetry exporter.

## What is verified

- `test_trace_structure.py`: one trace per graph run, a span per node in execution order,
  spans nested inside their parents, a paused graph not shown as an error, every span tied
  to the thread (`session.id`).
- `test_llm_spans.py`: each model call is a generation under its node, with model name,
  provider, parameters, token usage (when the model reports it) and latency. Its input and
  output are reduced to roles, so the forced tool call's arguments are not exported.
- `test_errors.py`: a failing node has an ERROR span named after it, its parents are marked,
  the trace keeps its thread, and the error's text is reduced to its type.
- `test_masking.py`: guests' free text, labels, corrections, accessibility needs and secrets
  (API key, database URL with password, Langfuse keys) never appear in span data.
- `test_tracing_switch.py`: tracing is off unless `PARKMIND_TRACING=langfuse`, LangSmith is
  refused, and a failing exporter, mask or handler leaves planning unchanged.

`obs_support.py` holds the harness: `tracing_to` points the production client at the
in-memory exporter, `RecordedClaude` is the real `ChatAnthropic` with its network call
replaced, and `planner` builds the initial planning graph on in-memory ports.

## Run

```
poetry run pytest tests/observability -q
```

No test needs a Langfuse server, an Anthropic key or a database.
