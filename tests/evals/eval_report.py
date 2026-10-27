"""One scored conversation, kept as a line of JSON so the dashboard and Langfuse read the same facts."""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from evals.eval_conversation import ConversationFlowMetric, conversation_test_case
from evals.eval_dag import naturalness_case, naturalness_metric
from evals.eval_judge import ClaudeJudge
from evals.eval_live import DIALOGUES, converse
from evals.eval_thresholds import JUDGED
from parkmind.agents.ask_prompt import ASK_PROMPT_VERSION
from parkmind.config.settings import settings

RESULTS = Path("build/evals/results.jsonl")


@dataclass
class TurnScore:
    turn: int
    text: str
    score: float
    passed: bool
    reason: str


@dataclass
class Result:
    batch: str
    run_id: str
    timestamp: str
    arm: str
    dialogue: str
    prompt_version: str
    generator_model: str
    judge_model: str
    transcript: list[list[str]]
    flow_passed: bool
    flow_reason: str
    turns: list[TurnScore] = field(default_factory=list)

    @property
    def naturalness(self) -> float:
        return sum(t.score for t in self.turns) / len(self.turns) if self.turns else 0.0

    @property
    def passed(self) -> bool:
        return self.flow_passed and all(t.passed for t in self.turns)


def score_conversation(
    batch: str, number: int, arm: str, dialogue: str, judge: ClaudeJudge
) -> Result:
    turns = converse(DIALOGUES[dialogue], arm)
    flow = ConversationFlowMetric()
    flow.measure(conversation_test_case(turns))
    scores: list[TurnScore] = []
    for position in range(1, len(turns), 2):
        history = "\n".join(f"{role}: {text}" for role, text in turns[:position])
        metric = naturalness_metric(judge)
        metric.measure(naturalness_case(history, turns[position][1]))
        scores.append(
            TurnScore(
                turn=position // 2 + 1,
                text=turns[position][1],
                score=float(metric.score or 0.0),
                passed=bool(metric.score is not None and metric.score >= JUDGED),
                reason=str(metric.reason or ""),
            )
        )
    return Result(
        batch=batch,
        run_id=f"{batch}/{arm}/{dialogue}/{number}",
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
        arm=arm,
        dialogue=dialogue,
        prompt_version=ASK_PROMPT_VERSION,
        generator_model=settings.ASK_MODEL,
        judge_model=judge.get_model_name(),
        transcript=[list(turn) for turn in turns],
        flow_passed=bool(flow.success),
        flow_reason=str(flow.reason),
        turns=scores,
    )


def new_batch() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def append(results: list[Result], path: Path = RESULTS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        for result in results:
            out.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")


def load(path: Path = RESULTS) -> list[Result]:
    if not path.exists():
        return []
    results = []
    for line in path.read_text(encoding="utf-8").splitlines():
        data = json.loads(line)
        data["turns"] = [TurnScore(**t) for t in data["turns"]]
        results.append(Result(**data))
    return results
