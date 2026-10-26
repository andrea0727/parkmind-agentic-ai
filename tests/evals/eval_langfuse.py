"""Publishes each scored conversation to Langfuse as a trace with scores, and the verdict of each arm per batch as session scores.

Re-send only the verdicts of every stored batch (the conversations are already there):

    set -a; . ./.env; set +a; PARKMIND_TRACING=langfuse PYTHONPATH=src:tests .venv/bin/python -m evals.eval_langfuse
"""

import os
from collections import defaultdict

from evals import eval_report
from evals.eval_dashboard import approved
from evals.eval_report import Result
from evals.eval_runs import pass_rate
from evals.eval_thresholds import PASS_RATE


def configured() -> bool:
    """Same opt-in as the app (ADR 0003): the keys alone send nothing, ``PARKMIND_TRACING=langfuse`` is needed too."""
    return os.getenv("PARKMIND_TRACING", "").strip().lower() == "langfuse" and all(
        os.getenv(name)
        for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
    )


def _host_url() -> str:
    """The .env URL is written for the app container; from the host the same server is on localhost."""
    return os.environ["LANGFUSE_BASE_URL"].replace("host.docker.internal", "localhost")


def publish(results: list[Result]) -> int:
    """Returns how many conversations were sent; 0 when Langfuse is not switched on.

    The dialogues are synthetic (written by a generator model, never real guests), so
    they are exported without ``mask_for_trace``; ADR 0003 records that exception.
    """
    if not configured():
        return 0
    from langfuse import Langfuse, propagate_attributes

    client = Langfuse(base_url=_host_url())
    for result in results:
        with (
            propagate_attributes(
                trace_name=f"eval/{result.arm}/{result.dialogue}",
                session_id=result.batch,
                tags=["eval", result.arm, result.prompt_version],
            ),
            client.start_as_current_observation(
                name=f"conversation/{result.dialogue}",
                as_type="span",
                input=[turn for turn in result.transcript if turn[0] == "guest"],
                output=[turn for turn in result.transcript if turn[0] != "guest"],
                metadata={
                    "arm": result.arm,
                    "batch": result.batch,
                    "prompt_version": result.prompt_version,
                    "generator_model": result.generator_model,
                    "judge_model": result.judge_model,
                },
            ) as span,
        ):
            span.score_trace(
                name="conversation_passed",
                value=1.0 if result.passed else 0.0,
                data_type="NUMERIC",
                comment=_summary(result),
            )
            span.score_trace(
                name="flow_ok",
                value=1.0 if result.flow_passed else 0.0,
                data_type="NUMERIC",
                comment=result.flow_reason,
            )
            span.score_trace(
                name="naturalness",
                value=round(result.naturalness, 3),
                data_type="NUMERIC",
                comment=" | ".join(
                    f"t{t.turn}: {t.reason[:200]}" for t in result.turns
                ),
            )
    client.flush()
    publish_verdicts(results, client)
    return len(results)


def publish_verdicts(results: list[Result], client=None) -> int:
    """One ``approved_<arm>`` (1 or 0) and ``pass_rate_<arm>`` score per batch and arm, on the batch session; re-sending overwrites."""
    if not configured():
        return 0
    from langfuse import Langfuse

    client = client or Langfuse(base_url=_host_url())
    groups: dict[tuple[str, str], list[Result]] = defaultdict(list)
    for result in results:
        groups[(result.batch, result.arm)].append(result)
    for (batch, arm), mine in groups.items():
        rate = pass_rate([r.passed for r in mine])
        client.create_score(
            name=f"approved_{arm}",
            value=1.0 if approved(mine, arm) else 0.0,
            session_id=batch,
            score_id=f"{batch}:approved:{arm}",
            data_type="NUMERIC",
            comment=f"{sum(r.passed for r in mine)}/{len(mine)} conversations pass; "
            f"approved when every dialogue reaches {PASS_RATE:.0%}",
        )
        client.create_score(
            name=f"pass_rate_{arm}",
            value=round(rate, 3),
            session_id=batch,
            score_id=f"{batch}:pass_rate:{arm}",
            data_type="NUMERIC",
        )
    client.flush()
    return len(groups)


def _summary(result: Result) -> str:
    return (
        "pass"
        if result.passed
        else "fail: "
        + (
            result.flow_reason
            if not result.flow_passed
            else "a turn scored below the threshold"
        )
    )


if __name__ == "__main__":
    print(f"verdicts sent for {publish_verdicts(eval_report.load())} batch/arm pairs")
