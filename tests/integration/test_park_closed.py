"""A closed park (past closing time) is reported up front and refuses planning in plain words."""

from datetime import timedelta

import pytest
from conversation_support import FIRST_REQUEST, MISSING, make_rig
from fastapi.testclient import TestClient
from planning_support import NOW, PARK, factory_for, seed_snapshot, snapshot_context

from parkmind.api.app import create_app
from parkmind.core.conversation import HardConstraintAnswer, NotExpectedError
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph import replanning_graph as rg
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.conversation import ConversationService
from parkmind.services.use_cases.describe_plan import DescribePlanUseCase
from parkmind.services.use_cases.park_status import ParkStatusUseCase

AFTER_CLOSING = PARK.closing_time + timedelta(minutes=30)


@pytest.fixture
def closed_client():
    rig = make_rig()
    factory = factory_for(rig.deps)
    checkpointer = default_checkpointer()
    initial = ipg.build_initial_planning_graph(
        rig.extractor,
        checkpointer=checkpointer,
        deps_factory=factory,
        clock=lambda: AFTER_CLOSING,
    )
    replanning = rg.build_replanning_graph(
        checkpointer, deps_factory=factory, clock=lambda: AFTER_CLOSING
    )
    app = create_app(
        ConversationService(initial, replanning, deps_factory=factory, clock=lambda: AFTER_CLOSING),
        DescribePlanUseCase(factory),
        park_status=ParkStatusUseCase(factory),
        clock=lambda: AFTER_CLOSING,
        env={},
        argv=[],
    )
    with TestClient(app) as client:
        yield client


def test_session_reports_the_closed_park(closed_client):
    park = closed_client.post("/api/session").json()["park"]
    assert park["closed"] is True


def test_session_reports_an_open_park():
    rig = make_rig()
    factory = factory_for(rig.deps)
    app = create_app(
        rig.service,
        DescribePlanUseCase(factory),
        park_status=ParkStatusUseCase(factory),
        clock=lambda: NOW,
        env={},
        argv=[],
    )
    with TestClient(app) as client:
        assert client.post("/api/session").json()["park"]["closed"] is False


def test_planning_after_closing_says_the_park_closed(closed_client):
    t = {"thread_id": closed_client.post("/api/session").json()["thread_id"]}
    first = closed_client.post("/api/message", params=t, json={"text": FIRST_REQUEST})
    assert first.json()["status"] == "needs_confirmation"
    turn = closed_client.post(
        "/api/constraints/confirm", params=t, json={"confirmed": True, "consent": True}
    ).json()
    assert closed_client.get("/api/proposal", params=t).status_code == 204
    assert "closed" in " ".join(turn["messages"]).lower()


def _closed_request(client):
    t = {"thread_id": client.post("/api/session").json()["thread_id"]}
    client.post("/api/message", params=t, json={"text": FIRST_REQUEST})
    turn = client.post(
        "/api/constraints/confirm", params=t, json={"confirmed": True, "consent": True}
    ).json()
    return t, turn


def test_a_closed_park_offers_a_provisional_plan(closed_client):
    _, turn = _closed_request(closed_client)
    assert turn["provisional_offer"] is True


def test_the_provisional_plan_previews_tomorrow_without_proposing_it(closed_client):
    t, _ = _closed_request(closed_client)

    reply = closed_client.post("/api/provisional", params=t)

    assert reply.status_code == 200
    body = reply.json()
    assert body["status"] == "done" and body["proposal"] is None
    text = " ".join(body["messages"])
    assert "Provisional plan for tomorrow" in text and "opens at 09:00" in text
    assert "only a preview" in text and "couldn't" not in text
    assert closed_client.get("/api/proposal", params=t).status_code == 204
    assert closed_client.get("/api/plan", params=t).status_code in (204, 404)


def test_the_provisional_response_carries_the_plan_for_my_plan(closed_client):
    t, _ = _closed_request(closed_client)

    body = closed_client.post("/api/provisional", params=t).json()

    assert body["provisional"]["plan"]["stops"]
    assert body["provisional"]["opens_at"].startswith(
        (PARK.opening_time + timedelta(days=1)).date().isoformat()
    )


def test_the_api_refuses_a_refresh_before_the_park_opens(closed_client):
    t, _ = _closed_request(closed_client)
    closed_client.post("/api/provisional", params=t)

    assert closed_client.post("/api/provisional/refresh", params=t).status_code == 409


def test_a_provisional_plan_needs_a_confirmed_request(closed_client):
    t = {"thread_id": closed_client.post("/api/session").json()["thread_id"]}

    assert closed_client.post("/api/provisional", params=t).status_code == 409


def _closed_service(*extractions):
    rig = make_rig(*extractions)
    factory = factory_for(rig.deps)
    checkpointer = default_checkpointer()
    initial = ipg.build_initial_planning_graph(
        rig.extractor, checkpointer=checkpointer, deps_factory=factory, clock=lambda: AFTER_CLOSING
    )
    replanning = rg.build_replanning_graph(
        checkpointer, deps_factory=factory, clock=lambda: AFTER_CLOSING
    )
    return ConversationService(initial, replanning, deps_factory=factory, clock=lambda: AFTER_CLOSING)


def test_the_first_question_of_a_closed_day_says_the_park_is_closed():
    service = _closed_service(MISSING["extraction"])

    turn = service.say(service.start(), " ".join(MISSING["messages"]))

    assert turn.status.value == "needs_answer"
    assert "closed" in turn.messages[0].lower() and turn.question not in turn.messages


def test_an_open_day_does_not_add_a_closed_notice():
    rig = make_rig(MISSING["extraction"])

    turn = rig.service.say(rig.service.start(), " ".join(MISSING["messages"]))

    assert turn.status.value == "needs_answer" and not turn.messages


class _Moving:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _moving_rig():
    clock = _Moving(AFTER_CLOSING)
    rig = make_rig(clock=clock)
    return rig.service, clock, rig


def _provisional_thread(service):
    thread_id = service.start()
    service.say(thread_id, FIRST_REQUEST)
    service.confirm_constraints(thread_id, HardConstraintAnswer(confirmed=True, consent=True))
    return thread_id, service.provisional(thread_id)


def test_the_provisional_plan_carries_the_plan_and_the_opening_time():
    service, _, _ = _moving_rig()

    _, preview = _provisional_thread(service)

    assert preview.provisional_plan is not None and preview.proposal is None
    assert preview.provisional_opens == PARK.opening_time + timedelta(days=1)


def test_a_refresh_before_the_park_opens_is_refused():
    service, _, _ = _moving_rig()
    thread_id, _ = _provisional_thread(service)

    with pytest.raises(NotExpectedError):
        service.refresh_provisional(thread_id)


def test_a_refresh_without_a_provisional_plan_is_refused():
    service, _, _ = _moving_rig()

    with pytest.raises(NotExpectedError):
        service.refresh_provisional(service.start())


def test_once_the_park_opens_the_provisional_plan_becomes_a_proposal():
    service, clock, rig = _moving_rig()
    thread_id, preview = _provisional_thread(service)
    clock.now = preview.provisional_opens + timedelta(minutes=5)
    seed_snapshot(
        rig.deps.snapshots,
        snapshot_context(snapshot_id="snap-open", retrieved_at=clock.now - timedelta(minutes=5)),
    )

    turn = service.refresh_provisional(thread_id)

    assert turn.status.value == "needs_approval" and turn.proposal is not None
    assert "opened" in turn.messages[0].lower()
    assert service.active_plan(thread_id) is None
    with pytest.raises(NotExpectedError):
        service.refresh_provisional(thread_id)
