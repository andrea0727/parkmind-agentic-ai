"""A closed park (past closing time) is reported up front and refuses planning in plain words."""

from datetime import timedelta

import pytest
from conversation_support import FIRST_REQUEST, make_rig
from fastapi.testclient import TestClient
from planning_support import NOW, PARK, factory_for

from parkmind.api.app import create_app
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


def test_a_provisional_plan_needs_a_confirmed_request(closed_client):
    t = {"thread_id": closed_client.post("/api/session").json()["thread_id"]}

    assert closed_client.post("/api/provisional", params=t).status_code == 409
