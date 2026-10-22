---
name: parkmind-evals
description: Run and read ParkMind's live LLM evaluations (conversation quality, fixed template vs LLM question writer), rebuild the dashboard and send scores to Langfuse. Use when asked to evaluate the chat, check whether a prompt change helped, or show real LLM results.
---

# ParkMind evals

What is measured lives in `tests/evals/`; pass/fail stays in pytest and CI. This skill only orchestrates live runs and reads the results.

## Run

```bash
set -a; . ./.env; set +a
env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN PYTHONPATH=src:tests \
  .venv/bin/python -m evals.run_live --runs 5
```

- Plays each dialogue (`unsure`, `english`, `clear`) `--runs` times in two arms: `fixed-template` (no LLM question writer) and `llm-ask`.
- Appends to `build/evals/results.jsonl`, rebuilds `build/evals/dashboard.html`, and sends traces and scores to Langfuse (http://localhost:3000, project `parkmind-local`) when the `LANGFUSE_*` variables are set. `--no-langfuse` skips that.
- The judge is `PARKMIND_JUDGE_MODEL` (default `claude-sonnet-5`), deliberately not the model that writes the questions.
- Gate in pytest: `PARKMIND_RUN_LIVE=1 ... pytest tests/evals/test_conversation_live.py` (pass rate over `PARKMIND_CONVERSATION_RUNS`, default 5, must reach 80%).

## The metrics

1. Conversation flow (deterministic): no question repeated, nothing the guests wrote echoed back.
2. Naturalness (DeepEval DAG in `eval_dag.py`): per assistant turn, a yes/no tree about parroting, uncertainty with options, and a human reaction plus one question.
3. Pass rate: share of conversations where both hold on every turn.

Add a metric only if it answers a question the three above cannot; keep the headline set to five or fewer.

## Reading a result

Open `build/evals/dashboard.html` (cards, pass rate by dialogue, history by prompt version, transcripts with each turn's judge reason). A change helped only if the `llm-ask` pass rate rose over several runs and the sample is the same size. If a metric looks wrong, read the transcript before touching the prompt: use the `eval-triage` agent to classify failures and `scenario-writer` to add a dialogue.
