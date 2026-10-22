"""Live: real conversations with the extractor and the question writer, scored as a pass rate over several runs."""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("deepeval")

from evals.eval_judge import ClaudeJudge
from evals.eval_live import DIALOGUES, LLM
from evals.eval_report import Result, score_conversation
from evals.eval_runs import meets, pass_rate, runs
from evals.eval_thresholds import PASS_RATE

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY"
    ),
]


def _results(dialogue: str) -> list[Result]:
    judge = ClaudeJudge()
    with ThreadPoolExecutor(max_workers=runs()) as pool:
        futures = [
            pool.submit(score_conversation, "pytest", n, LLM, dialogue, judge)
            for n in range(runs())
        ]
        return [future.result() for future in futures]


@pytest.mark.parametrize("dialogue", DIALOGUES)
def test_the_conversation_never_repeats_a_question_or_parrots_the_guests(
    dialogue: str,
) -> None:
    results = _results(dialogue)
    flows = [r.flow_passed for r in results]

    assert meets(flows), (
        f"flow pass rate {pass_rate(flows):.0%} < {PASS_RATE:.0%}: "
        + "; ".join(r.flow_reason for r in results if not r.flow_passed)
    )


@pytest.mark.parametrize("dialogue", DIALOGUES)
def test_most_conversations_sound_natural_to_the_judge_on_every_turn(
    dialogue: str,
) -> None:
    results = _results(dialogue)
    turns_ok = [all(t.passed for t in r.turns) for r in results]

    assert meets(turns_ok), (
        f"natural pass rate {pass_rate(turns_ok):.0%} < {PASS_RATE:.0%}: "
        + " | ".join(
            f"{t.text} -> {t.reason[:120]}"
            for r in results
            for t in r.turns
            if not t.passed
        )
    )
