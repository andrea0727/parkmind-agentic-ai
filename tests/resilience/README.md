# Reliability tests

What happens when a dependency fails, a message is replayed, a flow runs away or the process restarts.
Everything runs offline on the in-memory ports and scripted fakes: no network, database or API key.

```
poetry run pytest tests/resilience -q
```

## Failure modes covered

| Module | Failure | Expected behavior |
|---|---|---|
| `test_dependency_recovery.py` | Live park data down (unavailable, unknown schema, not found, refused, snapshot save failing) | A stored snapshot still plans; without one the guests get the unavailable message, no plan, no proposal, and no provider text leaks |
| | Flaky provider or store (fails once) | The next turn on the same thread plans, with one proposal |
| | Plan or proposal store failing | No half-written proposal; a failed decision write keeps the checkpoint and `invoke(None)` activates the same plan without asking again |
| | Model rate-limited, overloaded or timing out | Bounded retries, then a flagged unavailable message and no plan; a model that returns lets the thread plan |
| | Model returns no tool call or a malformed one | Bounded re-extraction, then a request to rephrase; never a plan |
| | Extractor raising a raw error | Fails typed, the checkpoint survives and the thread retries |
| | Slow provider, snapshot ids that lead nowhere | Planning completes; dangling ids count as no snapshot |
| `test_retry_idempotency.py` | Approval replayed (bare or keyed by interrupt id) | The plan is activated once; one behavior entry per guest |
| | Conflicting second decision | Refused, nothing changes |
| | Same event twice, in one batch, from another source, or after approval | One replan |
| | Re-ask loops (bad output, incomplete data, spent call budget) | A fixed number of model calls per turn, never more than the budget |
| `test_execution_limits.py` | Flow that never ends | `GraphRecursionError` at the step limit; every graph call runs under a wall-clock deadline |
| `test_configured_timeouts.py` | Timeouts and attempt counts | Reach the HTTP clients and the model clients; a dead provider fails typed after exactly the configured attempts, with exponential backoff |
| `test_checkpoint_recovery.py` | Process restart (new graph, same checkpointer) | Resumes at the pending approval and activates the same plan without calling the model; a restart before confirmation asks again because the staged accessibility flags are gone |

## Known gap

`test_an_event_whose_replan_failed_replans_when_it_is_sent_again` is a strict `xfail`: the event is
recorded before the replan runs, so when the replan fails on an outage the same event is later dropped
as a duplicate and never replans.

## Helpers

`res_faults.py` holds the fault wrappers (`FlakyPort`, `DownPort`, `SlowPort`, `CorruptPort`), the
scripted model, `FakeClock` and `run_with_deadline`. `res_harness.py` holds `Rig`: graph, ports and
checkpointer that a test can break, rebuild and resume.

## Not covered

Rolling back a database transaction after a failure inside it: the in-memory ports do not roll back.
Settings read from environment variables are not re-read here.
