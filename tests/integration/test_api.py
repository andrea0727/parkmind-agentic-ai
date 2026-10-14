"""HTTP API (P0-35): success paths, invalid requests, and the one-process guarantees."""

import ast
import json
from pathlib import Path

import pytest
from conversation_support import FIRST_REQUEST, make_rig
from fastapi.testclient import TestClient
from planning_support import NOW, factory_for

from parkmind.api.app import create_app
from parkmind.api.guard import MultipleWorkersError, ensure_single_worker
from parkmind.services.use_cases.describe_plan import DescribePlanUseCase

CONFIRM = {"confirmed": True, "consent": True}
API_DIR = Path(__file__).parents[2] / "src" / "parkmind" / "api"


def _app(rig, **kwargs):
    return create_app(
        rig.service,
        DescribePlanUseCase(factory_for(rig.deps)),
        clock=lambda: NOW,
        env={},
        argv=[],
        **kwargs,
    )


@pytest.fixture
def rig():
    return make_rig()


@pytest.fixture
def client(rig):
    with TestClient(_app(rig)) as c:
        yield c


def _session(client) -> dict:
    created = client.post("/api/session")
    assert created.status_code == 201
    return {"thread_id": created.json()["thread_id"]}


def _to_proposal(client) -> tuple[dict, dict]:
    t = _session(client)
    first = client.post("/api/message", params=t, json={"text": FIRST_REQUEST})
    assert first.json()["status"] == "needs_confirmation"
    turn = client.post("/api/constraints/confirm", params=t, json=CONFIRM)
    assert turn.status_code == 200
    return t, turn.json()


def test_the_whole_flow_over_http_activates_only_on_approval(client):
    t = _session(client)
    assert client.get("/api/proposal", params=t).status_code == 204
    assert client.get("/api/plan", params=t).status_code == 204

    first = client.post("/api/message", params=t, json={"text": FIRST_REQUEST}).json()
    assert first["status"] == "needs_confirmation"
    assert first["confirmation"]["echo"]

    turn = client.post("/api/constraints/confirm", params=t, json=CONFIRM).json()
    assert turn["status"] == "needs_approval"
    proposal = turn["proposal"]
    assert proposal["plan"]["stops"]
    assert all(s["title"] for s in proposal["plan"]["stops"])
    assert client.get("/api/plan", params=t).status_code == 204

    polled = client.get("/api/proposal", params=t)
    assert polled.status_code == 200
    assert polled.json()["proposal_id"] == proposal["proposal_id"]

    done = client.post(f"/api/proposal/{proposal['proposal_id']}/approve", params=t)

    assert done.status_code == 200
    assert done.json()["status"] == "done"
    active = client.get("/api/plan", params=t)
    assert active.status_code == 200
    assert active.json()["plan_id"] == proposal["plan"]["plan_id"]
    assert client.get("/api/proposal", params=t).status_code == 204


def test_rejecting_with_a_reason_activates_nothing(client):
    t, turn = _to_proposal(client)

    done = client.post(
        f"/api/proposal/{turn['proposal']['proposal_id']}/reject",
        params=t,
        json={"reason": "TOO_MUCH_WALKING"},
    )

    assert done.status_code == 200
    assert client.get("/api/plan", params=t).status_code == 204


def test_an_event_on_the_active_plan_returns_a_replan_proposal_with_a_diff(client):
    t, turn = _to_proposal(client)
    client.post(f"/api/proposal/{turn['proposal']['proposal_id']}/approve", params=t)
    first_stop = client.get("/api/plan", params=t).json()["stops"][0]

    replan = client.post(
        "/api/events",
        params=t,
        json={"type": "ATTRACTION_DOWN", "attraction_id": first_stop["node_id"]},
    )

    assert replan.status_code == 200
    body = replan.json()
    assert body["status"] == "needs_approval"
    assert first_stop["stop_id"] in {
        d["stop_id"] for d in body["proposal"]["diff"]["removed"]
    }
    assert body["proposal"]["metrics"]["wait_minutes_before"] is not None


def test_the_event_body_cannot_set_severity_or_replan_need(client):
    t = _session(client)

    response = client.post(
        "/api/events",
        params=t,
        json={"type": "ATTRACTION_DOWN", "attraction_id": "a", "severity": "HIGH"},
    )

    assert response.status_code == 422


def test_swagger_documents_every_route():
    rig = make_rig()
    with TestClient(_app(rig)) as client:
        spec = client.get("/openapi.json").json()
        assert client.get("/docs").status_code == 200

    assert spec["info"]["title"] == "ParkMind API"
    assert {
        "/api/session",
        "/api/message",
        "/api/constraints/confirm",
        "/api/proposal",
        "/api/proposal/{proposal_id}/{decision}",
        "/api/plan",
        "/api/events",
        "/api/events/confirm",
        "/api/health",
    } <= set(spec["paths"])


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/message", {"text": ""}),
        ("post", "/api/message", {}),
        ("post", "/api/message", {"text": "hi", "lang": "xx"}),
        ("post", "/api/constraints/confirm", {"consent": True}),
        ("post", "/api/events", {"type": "METEOR"}),
        ("post", "/api/events", {"type": "WAIT_SPIKE", "attraction_id": "a"}),
        (
            "post",
            "/api/events",
            {"type": "RAIN_INCOMING", "affected_hours": ["2026-10-12T10:00:00"]},
        ),
        ("post", "/api/proposal/p/maybe", None),
        ("post", "/api/events/confirm", {}),
    ],
)
def test_invalid_requests_are_422(client, method, path, body):
    t = _session(client)

    response = getattr(client, method)(
        path, params=t, **({"json": body} if body is not None else {})
    )

    assert response.status_code == 422


def test_a_missing_thread_id_is_422(client):
    assert client.post("/api/message", json={"text": "hi"}).status_code == 422
    assert client.get("/api/plan").status_code == 422


def test_an_unknown_session_is_404(client):
    t = {"thread_id": "thr_nope"}

    assert client.post("/api/message", params=t, json={"text": "hi"}).status_code == 404
    assert client.get("/api/proposal", params=t).status_code == 404
    assert client.get("/api/plan", params=t).status_code == 404


def test_answers_that_do_not_fit_are_409(client):
    t = _session(client)
    client.post("/api/message", params=t, json={"text": FIRST_REQUEST})

    assert client.post("/api/proposal/p/approve", params=t).status_code == 409
    assert (
        client.post("/api/message", params=t, json={"text": "more"}).status_code == 409
    )
    assert (
        client.post(
            "/api/events/confirm", params=t, json={"confirmed": True}
        ).status_code
        == 409
    )


def test_a_decision_on_another_proposal_is_409(client):
    t, _ = _to_proposal(client)

    assert client.post("/api/proposal/prop_other/approve", params=t).status_code == 409


def test_planning_unavailable_is_503(rig):
    from parkmind.services.use_cases.planning_deps import PlanningUnavailableError

    def down(*args, **kwargs):
        raise PlanningUnavailableError("postgres://user:secret@db.internal:5432 refused")

    rig.service.say = down  # type: ignore[method-assign]
    with TestClient(_app(rig)) as client:
        t = _session(client)

        response = client.post("/api/message", params=t, json={"text": "hi"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "secret" not in detail and "postgres" not in detail
    assert "Nothing was proposed" in detail


def test_a_service_outage_is_503_with_the_message_for_the_guests(rig):
    from parkmind.core.conversation import ServiceUnavailableError

    def down(*args, **kwargs):
        raise ServiceUnavailableError("No se propuso nada. Intenten de nuevo.")

    rig.service.say = down  # type: ignore[method-assign]
    with TestClient(_app(rig)) as client:
        t = _session(client)

        response = client.post("/api/message", params=t, json={"text": "hola"})

    assert response.status_code == 503
    assert response.json()["detail"] == "No se propuso nada. Intenten de nuevo."


def test_the_ui_is_served_next_to_the_api(rig, tmp_path):
    (tmp_path / "index.html").write_text("<h1>ParkMind</h1>")
    with TestClient(_app(rig, ui_dir=tmp_path)) as client:
        assert "ParkMind" in client.get("/").text
        assert client.get("/api/health").json() == {
            "status": "ok",
            "auth_required": False,
        }


# --- one process, one SessionMemory ---------------------------------------------------


@pytest.mark.parametrize(
    ("env", "argv"),
    [
        ({"WEB_CONCURRENCY": "2"}, []),
        ({"PARKMIND_WORKERS": "4"}, []),
        ({}, ["uvicorn", "x:app", "--workers", "3"]),
        ({}, ["uvicorn", "x:app", "--workers=2"]),
        ({}, ["gunicorn", "x:app", "-w", "2"]),
        ({}, ["gunicorn", "x:app", "-w2"]),
    ],
)
def test_several_workers_fail_loudly_at_startup(rig, env, argv):
    app = create_app(
        rig.service, DescribePlanUseCase(factory_for(rig.deps)), env=env, argv=argv
    )

    with (
        pytest.raises(MultipleWorkersError, match="one worker"),
        TestClient(app),
    ):
        pass


@pytest.mark.parametrize(
    ("env", "argv"),
    [
        ({}, []),
        ({"WEB_CONCURRENCY": "1"}, []),
        ({}, ["--workers", "1"]),
    ],
)
def test_one_worker_starts(env, argv):
    ensure_single_worker(env, argv)


def test_every_session_store_the_planner_builds_shares_one_session_memory(monkeypatch):
    from parkmind.services.use_cases import planning_deps
    from parkmind.services.use_cases.session_memory import SESSION_MEMORY

    class _Stub:
        def __init__(self, *a, **k): ...
        def __enter__(self):
            return self

        def __exit__(self, *a): ...

    for name in ("connect", "ThemeParksClient", "OpenMeteoClient", "RoutingClient"):
        monkeypatch.setattr(planning_deps, name, _Stub)
    monkeypatch.setattr(planning_deps, "magic_kingdom_knowledge_store", lambda: None)
    for repo in (
        "PostgresSnapshotRepository",
        "PostgresIdMappingRepository",
        "PostgresAttractionRepository",
        "PostgresPlanRepository",
        "PostgresProposalRepository",
        "PostgresEventRepository",
        "PostgresExecutionStateRepository",
        "SnapshotCollector",
    ):
        monkeypatch.setattr(planning_deps, repo, _Stub)

    with (
        planning_deps.default_planning_deps() as first,
        planning_deps.default_planning_deps() as second,
    ):
        assert first.sessions._memory is SESSION_MEMORY
        assert second.sessions._memory is SESSION_MEMORY


def test_the_server_builds_no_session_memory_of_its_own():
    server = Path(__file__).parents[2] / "server.py"
    offenders = [
        path
        for path in [*API_DIR.glob("*.py"), server]
        if path.exists() and "SessionMemory(" in path.read_text()
    ]
    assert offenders == []


# --- routes hold no planner logic ------------------------------------------------------

PLANNER_MODULES = (
    "parkmind.services.planning",
    "parkmind.services.personalization",
    "parkmind.services.clients",
    "parkmind.graph",
    "parkmind.agents",
    "parkmind.tools",
    "langgraph",
    "langchain",
)


def test_the_api_package_imports_no_planner_graph_or_client_code():
    for path in API_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                assert not name.startswith(PLANNER_MODULES), (
                    f"{path.name} imports {name}"
                )


def test_an_event_on_an_unknown_attraction_is_a_422_not_a_silent_ignore(client):
    t, turn = _to_proposal(client)
    client.post(f"/api/proposal/{turn['proposal']['proposal_id']}/approve", params=t)

    response = client.post(
        "/api/events",
        params=t,
        json={"type": "ATTRACTION_DOWN", "attraction_id": "not_a_ride"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "not_a_ride" in detail
    assert "The park has:" in detail


def test_an_edit_over_http_returns_a_new_proposal(client):
    t, turn = _to_proposal(client)
    old = turn["proposal"]
    dropped = old["plan"]["stops"][-1]["node_id"]

    edited = client.post(
        f"/api/proposal/{old['proposal_id']}/edit", params=t, json={"remove": [dropped]}
    )

    assert edited.status_code == 200
    body = edited.json()
    assert body["status"] == "needs_approval"
    assert body["proposal"]["proposal_id"] != old["proposal_id"]
    assert dropped not in [s["node_id"] for s in body["proposal"]["plan"]["stops"]]
    assert client.get("/api/plan", params=t).status_code == 204


def test_an_edit_that_does_not_fit_is_422_and_keeps_the_proposal(client):
    t, turn = _to_proposal(client)
    pid = turn["proposal"]["proposal_id"]

    bad = client.post(f"/api/proposal/{pid}/edit", params=t, json={"remove": ["nope"]})

    assert bad.status_code == 422 and bad.json()["detail"]
    assert client.get("/api/proposal", params=t).json()["proposal_id"] == pid
    assert client.post(f"/api/proposal/{pid}/approve", params=t).status_code == 200


def test_an_empty_edit_is_422(client):
    t, turn = _to_proposal(client)
    pid = turn["proposal"]["proposal_id"]

    assert client.post(f"/api/proposal/{pid}/edit", params=t).status_code == 422
    assert (
        client.post(f"/api/proposal/{pid}/edit", params=t, json={}).status_code == 422
    )


@pytest.mark.parametrize(
    ("env", "argv"),
    [({"WEB_CONCURRENCY": "auto"}, []), ({}, ["--workers", "many"]), ({}, ["-wx"])],
)
def test_an_unreadable_worker_count_fails_closed(env, argv):
    with pytest.raises(MultipleWorkersError, match="one worker"):
        ensure_single_worker(env, argv)


# --- what the API never returns ---------------------------------------------------------

NEVER_RETURNED = (
    "group_objective",
    "accessibility_requirements",
    "mobility_requirements",
    "LIMITED_WALKING",
    "guest_requirements",
)


def test_no_response_after_the_confirmation_carries_the_objective_or_accessibility_data(client):
    # The confirmation response itself is left out on purpose: it carries
    # ``accessibility_guests`` (guest ids only), which the UI needs to ask for consent.
    t, turn = _to_proposal(client)
    pid = turn["proposal"]["proposal_id"]
    bodies = [turn]
    bodies.append(client.get("/api/proposal", params=t).json())
    bodies.append(client.post(f"/api/proposal/{pid}/approve", params=t).json())
    bodies.append(client.get("/api/plan", params=t).json())
    bodies.append(client.get("/api/health").json())

    blob = json.dumps(bodies)

    assert [name for name in NEVER_RETURNED if name in blob] == []
