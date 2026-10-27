"""Park-data, repository and model failures: the user hears about it, nothing is half-activated, and a retry works."""

from typing import Any

import anthropic
import httpx
import pytest
from elicit_support import FakeExtractor
from langchain_core.messages import HumanMessage
from planning_support import FakeCollector, snapshot_context

from parkmind.agents import elicit_agent, llm_budget
from parkmind.agents.elicit_agent import (
    UNAVAILABLE_FLAG,
    AnthropicExtractor,
    ExtractorOutputError,
    ExtractorUnavailableError,
)
from parkmind.graph import initial_planning_graph as ipg
from parkmind.services.clients.themeparks_errors import (
    ThemeParksClientError,
    ThemeParksNotFoundError,
    ThemeParksSchemaError,
    ThemeParksUnavailableError,
)
from parkmind.services.ports import RepositoryUnavailableError
from parkmind.services.use_cases.collect_snapshot import CollectResult
from parkmind.services.use_cases.planning_deps import PlanningUnavailableError
from resilience.res_faults import (
    CorruptPort,
    DownPort,
    FakeClock,
    FlakyPort,
    Reply,
    ScriptedModel,
    SlowPort,
)
from resilience.res_harness import (
    APPROVE,
    COMPLETE,
    CONFIG,
    CONFIRM,
    THREAD_ID,
    Rig,
    approval_for,
)

PROVIDER_FAILURES: list[Exception] = [
    ThemeParksUnavailableError("provider down"),
    ThemeParksSchemaError("provider answered with an unknown shape"),
    ThemeParksNotFoundError("park not found"),
    ThemeParksClientError("provider refused"),
    RepositoryUnavailableError("snapshot store down while saving"),
]
IDS = [type(e).__name__ for e in PROVIDER_FAILURES]


def _last_message(result: Any) -> str:
    return str(result["messages"][-1].content)


def _fresh_collector() -> FakeCollector:
    live = snapshot_context(snapshot_id="fresh")
    return FakeCollector(CollectResult("fresh", created=True, live_context=live))


@pytest.mark.parametrize("error", PROVIDER_FAILURES, ids=IDS)
def test_a_provider_failure_still_plans_from_the_stored_snapshot(
    error: Exception,
) -> None:
    collector = DownPort(FakeCollector(error=error), error, methods={"collect"})
    rig = Rig(collector=collector)

    result = rig.confirm()

    assert collector.failed == 1
    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert rig.state()["check_result"].valid
    assert rig.active is None


@pytest.mark.parametrize("error", PROVIDER_FAILURES, ids=IDS)
def test_a_provider_failure_without_a_snapshot_ends_with_a_message_and_no_plan(
    error: Exception,
) -> None:
    rig = Rig(collector=FakeCollector(error=error), snapshot=False)

    result = rig.confirm()

    assert "__interrupt__" not in result
    assert _last_message(result) == ipg.UNAVAILABLE_MESSAGE
    assert rig.active is None
    assert rig.proposals == []
    assert not rig.state().get("candidate_plan")


def test_the_unavailable_message_does_not_leak_the_provider_error() -> None:
    error = ThemeParksUnavailableError("https://user:secret@provider.internal refused")
    rig = Rig(collector=FakeCollector(error=error), snapshot=False)

    result = rig.confirm()

    assert "secret" not in _last_message(result)
    assert "provider.internal" not in _last_message(result)


def test_a_flaky_provider_recovers_on_the_next_turn_with_one_proposal() -> None:
    collector = FlakyPort(
        _fresh_collector(),
        ThemeParksUnavailableError("provider down"),
        failures=1,
        methods={"collect"},
    )
    rig = Rig(collector=collector, snapshot=False)
    failed = rig.confirm()
    assert _last_message(failed) == ipg.UNAVAILABLE_MESSAGE

    rig.invoke({"messages": [HumanMessage(content="Please try again.")]})
    result = rig.invoke(CONFIRM)

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert collector.failed == 1
    assert len(rig.proposals) == 1
    assert rig.active is None


def test_a_snapshot_store_that_fails_mid_run_lets_the_next_turn_plan() -> None:
    rig = Rig()
    flaky = FlakyPort(
        rig.deps.snapshots,
        RepositoryUnavailableError("snapshot store down"),
        failures=1,
        methods={"recent_ids"},
    )
    rig.swap(snapshots=flaky)
    failed = rig.confirm()
    assert _last_message(failed) == ipg.UNAVAILABLE_MESSAGE

    rig.invoke({"messages": [HumanMessage(content="Please try again.")]})
    result = rig.invoke(CONFIRM)

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert len(rig.proposals) == 1


def test_a_plan_store_that_fails_while_proposing_leaves_no_pending_proposal() -> None:
    rig = Rig()
    flaky = FlakyPort(
        rig.deps.plans,
        RepositoryUnavailableError("plan store down"),
        failures=1,
        methods={"save"},
    )
    rig.swap(plans=flaky)

    result = rig.confirm()

    assert "__interrupt__" not in result
    assert _last_message(result) == ipg.UNAVAILABLE_MESSAGE
    assert rig.proposals == []
    assert rig.active is None


def test_a_failed_decision_write_resumes_on_the_same_thread_without_asking_again() -> (
    None
):
    rig = Rig()
    pending = rig.confirm()["__interrupt__"][0]
    candidate = rig.state()["candidate_plan"]
    asked = len(rig.extractor.calls)
    flaky = FlakyPort(
        rig.deps.proposals,
        RepositoryUnavailableError("proposal store down"),
        failures=1,
        methods={"resolve"},
    )
    rig.swap(proposals=flaky)

    with pytest.raises(PlanningUnavailableError):
        rig.invoke(approval_for(pending.id))

    assert rig.active is None
    assert [p.approval_status.value for p in rig.proposals] == ["PENDING"]
    assert rig.state()["constraints"] is not None

    result = rig.invoke(None)

    assert "__interrupt__" not in result
    assert rig.active.plan_id == candidate.plan_id
    assert [p.approval_status.value for p in rig.proposals] == ["APPROVED"]
    assert len(rig.extractor.calls) == asked
    assert rig.graph.get_state(CONFIG).config["configurable"]["thread_id"] == THREAD_ID


def test_a_proposal_store_that_stays_down_never_activates_a_plan() -> None:
    rig = Rig()
    pending = rig.confirm()["__interrupt__"][0]
    down = DownPort(
        rig.deps.proposals,
        RepositoryUnavailableError("proposal store down"),
        methods={"resolve"},
    )
    rig.swap(proposals=down)

    with pytest.raises(PlanningUnavailableError):
        rig.invoke(approval_for(pending.id))
    with pytest.raises(PlanningUnavailableError):
        rig.invoke(None)

    assert rig.active is None
    down.recover()
    rig.invoke(None)
    assert rig.active is not None


# --- the model fails ---------------------------------------------------------------------


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.invalid/v1/messages")


def _rate_limited() -> anthropic.RateLimitError:
    response = httpx.Response(429, request=_request())
    return anthropic.RateLimitError("rate limited", response=response, body=None)


def _server_error() -> anthropic.InternalServerError:
    response = httpx.Response(500, request=_request())
    return anthropic.InternalServerError("overloaded", response=response, body=None)


def _timeout() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=_request())


def _extractor(
    model: ScriptedModel, monkeypatch: pytest.MonkeyPatch
) -> AnthropicExtractor:
    monkeypatch.setattr(llm_budget.time, "sleep", lambda _seconds: None)
    extractor = AnthropicExtractor(api_key="test-key")
    extractor._llm = model
    return extractor


@pytest.mark.parametrize(
    "failure",
    [_rate_limited, _server_error, _timeout],
    ids=["rate_limit", "overloaded", "timeout"],
)
def test_a_model_that_keeps_failing_is_retried_a_bounded_number_of_times(
    failure: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedModel(failure())
    extractor = _extractor(model, monkeypatch)

    with pytest.raises(ExtractorUnavailableError):
        extractor.extract(["hi"])

    assert model.calls == elicit_agent.SDK_RETRIES + 1


def test_a_model_that_is_rate_limited_once_recovers_within_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = Reply([{"args": dict(COMPLETE["extraction"])}])
    model = ScriptedModel(_rate_limited(), reply)
    extractor = _extractor(model, monkeypatch)

    assert extractor.extract(["hi"]) == COMPLETE["extraction"]
    assert model.calls == 2


@pytest.mark.parametrize(
    "failure",
    [_rate_limited, _server_error, _timeout],
    ids=["rate_limit", "overloaded", "timeout"],
)
def test_a_model_outage_ends_the_turn_with_a_message_and_no_plan(
    failure: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    extractor = _extractor(ScriptedModel(failure()), monkeypatch)
    rig = Rig(extractor=extractor)

    result = rig.start()

    assert "__interrupt__" not in result
    assert result["messages"][-1].additional_kwargs[UNAVAILABLE_FLAG]
    assert rig.state().get("constraints") is None
    assert rig.proposals == []
    assert rig.active is None


def test_a_model_that_comes_back_lets_the_same_thread_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = Reply([{"args": dict(COMPLETE["extraction"])}])
    model = ScriptedModel(*([_timeout()] * (elicit_agent.SDK_RETRIES + 1)), reply)
    rig = Rig(extractor=_extractor(model, monkeypatch))
    rig.start()

    rig.invoke({"messages": [HumanMessage(content="Please try again.")]})
    result = rig.invoke(CONFIRM)

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert len(rig.proposals) == 1


@pytest.mark.parametrize(
    "reply",
    [
        Reply([]),
        Reply([{"args": "not a mapping"}]),
        Reply([{"args": {"guests": "x"}}]),
    ],
    ids=["no_tool_call", "args_not_a_mapping", "wrong_shape"],
)
def test_malformed_model_output_asks_the_guests_to_rephrase_and_plans_nothing(
    reply: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedModel(reply)
    rig = Rig(extractor=_extractor(model, monkeypatch))

    result = rig.start()

    assert result["__interrupt__"][0].value["missing"] == ["unreadable_response"]
    assert model.calls == elicit_agent.DEFAULT_MAX_ATTEMPTS
    assert rig.proposals == []
    assert rig.active is None


def test_a_model_output_error_from_a_custom_extractor_is_handled_like_a_bad_answer() -> (
    None
):
    rig = Rig(extractor=FakeExtractor(ExtractorOutputError("no structured output")))

    result = rig.start()

    assert result["__interrupt__"][0].value["missing"] == ["unreadable_response"]
    assert len(rig.extractor.calls) == elicit_agent.DEFAULT_MAX_ATTEMPTS


@pytest.mark.parametrize(
    "error", [TimeoutError("model timed out"), ConnectionError("model unreachable")]
)
def test_an_extractor_that_raises_a_raw_error_fails_typed_and_the_thread_can_retry(
    error: Exception,
) -> None:
    extractor = FakeExtractor(error, COMPLETE["extraction"])
    rig = Rig(extractor=extractor)

    with pytest.raises(type(error)):
        rig.start()

    assert rig.proposals == []
    assert rig.active is None
    result = rig.invoke(None)
    assert result["__interrupt__"][0].value["kind"] == "hard_constraint_confirmation"
    rig.invoke(CONFIRM)
    assert len(rig.proposals) == 1
    assert rig.active is None
    rig.invoke(APPROVE)
    assert rig.active is not None


# --- slow and corrupt dependencies -------------------------------------------------------------


def test_a_slow_provider_still_plans_and_the_delay_is_paid_once() -> None:
    clock = FakeClock()
    collector = SlowPort(_fresh_collector(), clock, 30.0, methods={"collect"})
    rig = Rig(collector=collector, snapshot=False)

    result = rig.confirm()

    assert result["__interrupt__"][0].value["kind"] == "plan_approval"
    assert collector.calls == {"collect": 1}
    assert clock.now == 30.0


def test_stored_snapshot_ids_that_lead_nowhere_are_treated_as_no_snapshot() -> None:
    rig = Rig(collector=FakeCollector(error=ThemeParksUnavailableError("down")))
    dangling = CorruptPort(rig.deps.snapshots, None, methods={"get"})
    rig.swap(snapshots=dangling)

    result = rig.confirm()

    assert _last_message(result) == ipg.UNAVAILABLE_MESSAGE
    assert rig.proposals == []
    assert rig.active is None
