"""A thread that has spent its LLM call budget makes no more calls and still completes [G12]."""

import contextlib
from datetime import date, datetime
from typing import Any

import anthropic
import factories
import httpx
import pytest
from elicit_support import make_intake, make_names
from langchain_core.messages import HumanMessage

from parkmind.agents import llm_budget
from parkmind.agents.ask_agent import ASK_BUDGET, AnthropicQuestionWriter
from parkmind.agents.elicit_agent import (
    AnthropicExtractor,
    ExtractorUnavailableError,
    make_elicit_node,
)
from parkmind.agents.explain_agent import (
    TEMPLATE_VERSION,
    AnthropicNarrator,
    explain_grounded,
)
from parkmind.agents.llm_budget import (
    LlmBudgetExhaustedError,
    LlmCallBudget,
    bound_to_thread,
    invoke_counted,
)
from parkmind.core.contracts import PARK_TZ, CheckResult
from parkmind.services.use_cases.explain_plan import (
    PreparedExplanation,
    explain_plan,
    explanation_payload,
)

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=PARK_TZ)
GROUNDED = "Space Mountain first: a 20 minute wait and a 5 minute walk."
EXTRACTION = {
    "guests": [{"role": "adult"}],
    "departure_time": "18:00",
}


class _Response:
    def __init__(self, tool_calls: list[dict[str, Any]]) -> None:
        self.tool_calls = tool_calls


class _Llm:
    """Stands in for the bound chat model: counts the calls that reach it."""

    def __init__(self, *script: Any) -> None:
        self._script = list(script)
        self.calls = 0

    def invoke(self, _messages: Any) -> Any:
        self.calls += 1
        answer = self._script.pop(0) if self._script else _Response([])
        if isinstance(answer, Exception):
            raise answer
        return answer


def _transient() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.invalid")
    )


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_budget.time, "sleep", lambda _s: None)


def _spent(thread_id: str, limit: int = 3) -> LlmCallBudget:
    budget = LlmCallBudget(limit)
    for _ in range(limit):
        assert budget.acquire(thread_id)
    return budget


# --- the counter ------------------------------------------------------------------


def test_each_thread_has_its_own_budget() -> None:
    budget = LlmCallBudget(2)

    assert budget.acquire("a") and budget.acquire("a")
    assert not budget.acquire("a")
    assert budget.acquire("b")
    assert (budget.used("a"), budget.used("b")) == (2, 1)


def test_a_refused_call_is_not_counted() -> None:
    budget = _spent("a", 2)

    assert not budget.acquire("a")
    assert budget.used("a") == 2


def test_the_budget_must_allow_a_call() -> None:
    with pytest.raises(ValueError):
        LlmCallBudget(0)


def test_calls_with_no_thread_bound_are_not_counted() -> None:
    llm, budget = _Llm(_Response([])), LlmCallBudget(1)

    invoke_counted(llm, [], retries=0, budget=budget)
    invoke_counted(llm, [], retries=0, budget=budget)

    assert llm.calls == 2


# --- retries count like any other call ----------------------------------------------


def test_every_retry_spends_budget() -> None:
    llm, budget = _Llm(_transient(), _transient(), _Response([])), LlmCallBudget(10)

    with bound_to_thread("t1"):
        invoke_counted(llm, [], retries=2, budget=budget)

    assert llm.calls == 3 and budget.used("t1") == 3


def test_retries_stop_when_the_budget_runs_out_mid_failure() -> None:
    llm, budget = _Llm(_transient(), _transient(), _transient()), LlmCallBudget(2)

    with bound_to_thread("t1"), pytest.raises(LlmBudgetExhaustedError):
        invoke_counted(llm, [], retries=5, budget=budget)

    assert llm.calls == 2 and budget.used("t1") == 2


def test_a_failure_that_is_not_transient_is_not_retried() -> None:
    llm, budget = _Llm(ValueError("bad")), LlmCallBudget(10)

    with bound_to_thread("t1"), pytest.raises(ValueError):
        invoke_counted(llm, [], retries=3, budget=budget)

    assert llm.calls == 1


def test_a_failure_after_the_last_retry_is_raised() -> None:
    llm = _Llm(_transient(), _transient())

    with pytest.raises(anthropic.APIConnectionError):
        invoke_counted(llm, [], retries=1, budget=LlmCallBudget(10))

    assert llm.calls == 2


# --- elicit: ask again --------------------------------------------------------------


def _extractor(llm: _Llm, budget: LlmCallBudget) -> AnthropicExtractor:
    extractor = AnthropicExtractor(api_key="k", budget=budget)
    extractor._runnable = lambda: llm  # type: ignore[method-assign]
    return extractor


def test_the_extractor_makes_no_call_once_the_thread_is_at_budget() -> None:
    llm = _Llm(_Response([{"args": EXTRACTION}]))
    extractor = _extractor(llm, _spent("t1", 1))

    with bound_to_thread("t1"), pytest.raises(LlmBudgetExhaustedError):
        extractor.extract(["hi"])

    assert llm.calls == 0


def test_a_spent_budget_is_not_reported_as_an_unreachable_model() -> None:
    assert not issubclass(LlmBudgetExhaustedError, ExtractorUnavailableError)


def test_an_elicit_turn_at_budget_asks_again_without_calling_the_model() -> None:
    llm = _Llm(_Response([{"args": EXTRACTION}]))
    intake, _ = make_intake()
    node = make_elicit_node(
        _extractor(llm, _spent("t1", 2)),
        intake,
        make_names(),
        today=lambda: date(2026, 10, 6),
        now=lambda: NOW,
    )

    update = node(
        {
            "thread_id": "t1",
            "messages": [HumanMessage(content="Just me, leaving at 6 PM")],
        }
    )

    assert llm.calls == 0
    assert update["constraints"] is None
    message = update["messages"][0]
    assert message.additional_kwargs["missing_information"] == ["unreadable_response"]


def test_another_thread_is_not_affected() -> None:
    llm = _Llm(_Response([{"args": EXTRACTION}]))
    intake, _ = make_intake()
    node = make_elicit_node(
        _extractor(llm, _spent("t1", 2)),
        intake,
        make_names(),
        today=lambda: date(2026, 10, 6),
        now=lambda: NOW,
    )

    update = node(
        {
            "thread_id": "t2",
            "messages": [HumanMessage(content="Just me, leaving at 6 PM")],
        }
    )

    assert llm.calls == 1 and update["constraints"] is not None


# --- explain: the template ------------------------------------------------------------


def _prepared() -> PreparedExplanation:
    check = CheckResult(valid=True)
    names = {"a1": "Space Mountain"}
    plan = factories.plan()
    return PreparedExplanation(
        explanation_payload(plan, check, names), explain_plan(plan, check, names)
    )


def _narrator(llm: _Llm, budget: LlmCallBudget) -> AnthropicNarrator:
    narrator = AnthropicNarrator(api_key="k", budget=budget)
    narrator._runnable = lambda: llm  # type: ignore[method-assign]
    return narrator


def test_explain_at_budget_writes_the_template_without_calling_the_model() -> None:
    llm = _Llm(_Response([{"args": {"text": GROUNDED}}]))
    prepared = _prepared()

    result = explain_grounded(_narrator(llm, _spent("t1", 2)), prepared, thread_id="t1")

    assert llm.calls == 0
    assert (result.source, result.prompt_version) == ("template", TEMPLATE_VERSION)
    assert result.text == prepared.template


def test_explain_with_budget_left_still_uses_the_model() -> None:
    llm = _Llm(_Response([{"args": {"text": GROUNDED}}]))

    result = explain_grounded(
        _narrator(llm, LlmCallBudget(2)), _prepared(), thread_id="t1"
    )

    assert (result.source, result.attempts, llm.calls) == ("llm", 1, 1)


def test_explain_stops_retrying_when_the_budget_ends_mid_repair() -> None:
    invented = _Response([{"args": {"text": "Space Mountain: a 45 minute wait."}}])
    llm = _Llm(invented, invented)
    budget = LlmCallBudget(1)

    result = explain_grounded(_narrator(llm, budget), _prepared(), thread_id="t1")

    assert result.source == "template" and llm.calls == 1
    assert budget.used("t1") == 1


# --- ask has a budget of its own ---------------------------------------------------


def test_the_question_writer_spends_its_own_budget_not_the_shared_one() -> None:
    llm = _Llm(_Response([{"args": {}}]), _Response([{"args": {}}]))
    writer = AnthropicQuestionWriter(api_key="k")
    writer._runnable = lambda: llm  # type: ignore[method-assign]
    before = (llm_budget.DEFAULT_BUDGET.used("ask-t"), ASK_BUDGET.used("ask-t"))

    with bound_to_thread("ask-t"):
        for _ in range(2):
            with contextlib.suppress(Exception):
                writer.write("{}")

    assert llm.calls == 2
    assert llm_budget.DEFAULT_BUDGET.used("ask-t") == before[0]
    assert ASK_BUDGET.used("ask-t") == before[1] + 2


def test_the_question_writer_stops_at_its_own_budget() -> None:
    llm = _Llm(_Response([{"args": {}}]))
    writer = AnthropicQuestionWriter(api_key="k", budget=_spent("t1", 1))
    writer._runnable = lambda: llm  # type: ignore[method-assign]

    with bound_to_thread("t1"), pytest.raises(LlmBudgetExhaustedError):
        writer.write("{}")

    assert llm.calls == 0


def test_the_counts_are_logged_without_the_thread(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger=llm_budget.logger.name)

    assert not _spent("secret-thread-id", 2).acquire("secret-thread-id")

    assert "2 of 2" in caplog.text
    assert "secret-thread-id" not in caplog.text
