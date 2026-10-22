# LLM evaluation tests

Layer 3 of the test strategy: is what the models produce good, not only well formed. Most checks are deterministic and run offline; the ones that need a real model are marked `live`.

## Run

```bash
poetry install --with eval                       # optional: adds DeepEval; without it these tests skip
poetry run pytest tests/evals                    # offline, no API key, live tests skipped
PARKMIND_RUN_LIVE=1 poetry run pytest tests/evals -m live   # real extraction and judge calls (needs ANTHROPIC_API_KEY)
```

`PARKMIND_EVAL_RUNS` sets how many times the live consistency check repeats an extraction (default 3).

## What is checked

| Module | Proves |
| --- | --- |
| `test_deterministic_metrics.py` | Groundedness (every number, time and attraction in an explanation exists in the plan) and constraint preservation (party size, heights, departure, must-do, avoid) pass on faithful output and fail on an invented wait, a changed height or a dropped must-do. |
| `test_tool_call_correctness.py` | The extractor calls the extraction tool exactly once, with arguments valid for `ElicitationExtraction`, and no other tool, using a scripted model. |
| `test_accuracy_labeled_set.py` | Replaying `tests/fixtures/elicit/scenarios.json` through validation and `build_party_constraints` keeps every hand-labeled constraint (`fixtures/constraint_expectations.json`). |
| `test_consistency.py` | The same extraction repeated N times leaves every hard constraint identical; a scripted flaky extractor shows a flip is detected. |
| `test_judged_metrics.py` | The judged metrics (instruction following, language, relevancy, faithfulness) are built with the agreed rubric, thresholds and judge; the live class scores them. |
| `test_conversation_flow.py` | The conversation flow check flags a repeated question or an echo of the guests' words. |
| `test_dag_metric.py` | The naturalness decision tree scores by the path of its yes/no answers, using a scripted judge. |
| `test_conversation_live.py` | Live: real conversations in the `llm-ask` arm must reach a pass rate of 80% over `PARKMIND_CONVERSATION_RUNS` runs (default 5), for flow and for per-turn naturalness. |
| `test_judge.py` | `ClaudeJudge` builds without a key, parses schema replies, and rejects replies that do not fit. |

Pass marks are in `eval_thresholds.py`.

## Live results, dashboard and Langfuse

```bash
set -a; . ./.env; set +a
env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN PYTHONPATH=src:tests .venv/bin/python -m evals.run_live --runs 5
```

Plays every dialogue in `eval_live.py` several times in two arms, the fixed question template and the LLM question writer, and scores each conversation: no repeated question, no echo, and a naturalness score per turn from a DeepEval DAG (`eval_dag.py`). Results are appended to `build/evals/results.jsonl`, `build/evals/dashboard.html` is rebuilt (open it in a browser: pass rate per arm and dialogue, history by prompt version, every transcript with the judge's reason per turn), and when the `LANGFUSE_*` variables are set each conversation is sent to Langfuse as a trace with scores `conversation_passed`, `flow_ok` and `naturalness`, grouped as a session per batch.

The headline metrics are three: conversation flow, naturalness and the pass rate over runs. `.claude/skills/parkmind-evals` describes the workflow for Claude Code, and the `eval-triage` and `scenario-writer` agents classify failures and propose new dialogues.

## Notes

- The judge is `ClaudeJudge` (`eval_judge.py`), built on the project's Anthropic settings. Every metric gets it explicitly, so DeepEval never falls back to an OpenAI model. Its model is `PARKMIND_JUDGE_MODEL` (default `claude-sonnet-5`), on purpose a different model from the Haiku that writes and extracts, because a model grades its own output too kindly.
- `conftest.py` turns off DeepEval telemetry, `.env` loading and update checks, and points its state at the system temp directory.
