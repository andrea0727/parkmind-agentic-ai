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
| `test_judge.py` | `ClaudeJudge` builds without a key, parses schema replies, and rejects replies that do not fit. |

Pass marks are in `eval_thresholds.py`.

## Notes

- The judge is `ClaudeJudge` (`eval_judge.py`), built on the project's Anthropic settings. Every metric gets it explicitly, so DeepEval never falls back to an OpenAI model.
- `conftest.py` turns off DeepEval telemetry, `.env` loading and update checks, and points its state at the system temp directory.
