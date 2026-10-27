"""Runs the live conversations in both arms, stores the results, rebuilds the dashboard and sends scores to Langfuse.

    set -a; . ./.env; set +a
    env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN PYTHONPATH=src:tests \
        .venv/bin/python -m evals.run_live --runs 5
"""

import argparse
from concurrent.futures import ThreadPoolExecutor

from evals import eval_dashboard, eval_langfuse, eval_report
from evals.eval_judge import ClaudeJudge
from evals.eval_live import ARMS, DIALOGUES
from evals.eval_runs import pass_rate, runs
from evals.eval_thresholds import PASS_RATE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runs", type=int, default=runs(), help="conversations per dialogue and arm"
    )
    parser.add_argument("--arms", nargs="+", default=list(ARMS), choices=ARMS)
    parser.add_argument(
        "--dialogues", nargs="+", default=list(DIALOGUES), choices=list(DIALOGUES)
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--no-langfuse", action="store_true")
    args = parser.parse_args()

    batch, judge = eval_report.new_batch(), ClaudeJudge()
    jobs = [
        (n, arm, dialogue)
        for arm in args.arms
        for dialogue in args.dialogues
        for n in range(args.runs)
    ]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(eval_report.score_conversation, batch, n, arm, dialogue, judge)
            for n, arm, dialogue in jobs
        ]
        results = [future.result() for future in futures]

    eval_report.append(results)
    path = eval_dashboard.write(eval_report.load())
    sent = 0 if args.no_langfuse else eval_langfuse.publish(results)

    for arm in args.arms:
        mine = [r for r in results if r.arm == arm]
        verdict = (
            "APPROVED" if eval_dashboard.approved(results, arm) else "NOT APPROVED"
        )
        print(
            f"{arm:15} {verdict:12} {sum(r.passed for r in mine)}/{len(mine)} conversations pass "
            f"({pass_rate([r.passed for r in mine]):.0%}, target {PASS_RATE:.0%} in every dialogue)"
        )
    print(f"dashboard: {path.resolve()}")
    print(
        f"langfuse: {sent} conversations sent"
        if sent
        else "langfuse: not configured or skipped"
    )


if __name__ == "__main__":
    main()
