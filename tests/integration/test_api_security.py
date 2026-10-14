"""API security: login, bearer enforcement, session ownership, rate limits, headers, config."""

import sys
from pathlib import Path

import jwt
import pytest
from conversation_support import FIRST_REQUEST, make_rig
from fastapi.testclient import TestClient
from planning_support import NOW, factory_for

from parkmind.api.app import create_app
from parkmind.api.security import (
    AuthConfig,
    AuthConfigError,
    Limits,
    RateLimiter,
    hash_password,
    parse_users,
    verify_password,
)
from parkmind.services.use_cases.describe_plan import DescribePlanUseCase

SECRET = "s" * 40
ROOT = Path(__file__).parents[2]
HASHES = {"alice": hash_password("alice-pass-1"), "bob": hash_password("bob-pass-22")}


def _auth(clock=None, **kwargs) -> AuthConfig:
    extra = {"clock": clock} if clock else {}
    return AuthConfig(HASHES, SECRET, **extra, **kwargs)


def _client(auth: AuthConfig | None = None, limits: Limits | None = None) -> TestClient:
    rig = make_rig()
    app = create_app(
        rig.service,
        DescribePlanUseCase(factory_for(rig.deps)),
        clock=lambda: NOW,
        env={},
        argv=[],
        auth=auth or _auth(),
        limits=limits,
    )
    return TestClient(app)


def _login(client, user="alice", password="alice-pass-1"):
    return client.post("/api/login", json={"username": user, "password": password})


def _bearer(client, user="alice", password="alice-pass-1") -> dict:
    token = _login(client, user, password).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- passwords -------------------------------------------------------------------------


def test_password_hashes_verify_and_are_salted():
    a, b = hash_password("correct horse"), hash_password("correct horse")
    assert a != b
    assert verify_password("correct horse", a)
    assert not verify_password("wrong horse", a)


@pytest.mark.parametrize(
    "garbage", ["", "plain", "scrypt$x", "md5$1$2$3$4$5", "scrypt$a$b$c$d$e"]
)
def test_malformed_stored_hashes_never_verify(garbage):
    assert not verify_password("anything", garbage)


def test_parse_users_accepts_hashes_and_plain_passwords():
    users = parse_users(f"alice={HASHES['alice']}, bob=bob-pass-22")
    assert set(users) == {"alice", "bob"}
    assert users["alice"] == HASHES["alice"]
    assert users["bob"].startswith("scrypt$") and verify_password(
        "bob-pass-22", users["bob"]
    )


@pytest.mark.parametrize(
    "raw", ["  ", "alice", "alice=", "=secret-pass", "alice=short"]
)
def test_parse_users_rejects_unusable_entries(raw):
    with pytest.raises(AuthConfigError):
        parse_users(raw)


def test_a_malformed_stored_hash_is_refused_at_startup():
    with pytest.raises(AuthConfigError):
        parse_users("alice=scrypt$truncated")


def test_a_short_secret_is_refused():
    with pytest.raises(AuthConfigError):
        AuthConfig(HASHES, "short")


# --- tokens ----------------------------------------------------------------------------


def test_tokens_expire():
    now = [1_000_000.0]
    auth = _auth(clock=lambda: now[0], token_ttl_seconds=60)
    token = auth.issue_token("alice")
    assert auth.verify_token(token) == "alice"
    now[0] += 61
    assert auth.verify_token(token) is None


def test_forged_unsigned_and_foreign_tokens_are_rejected():
    auth = _auth()
    claims = {"sub": "alice", "iss": "parkmind", "exp": 9_999_999_999}
    assert auth.verify_token(jwt.encode(claims, "x" * 40, algorithm="HS256")) is None
    assert auth.verify_token(jwt.encode(claims, key=None, algorithm="none")) is None
    assert auth.verify_token(jwt.encode({**claims, "iss": "other"}, SECRET)) is None
    assert auth.verify_token(jwt.encode({**claims, "sub": "mallory"}, SECRET)) is None
    no_exp = {"sub": "alice", "iss": "parkmind"}
    assert auth.verify_token(jwt.encode(no_exp, SECRET)) is None
    assert auth.verify_token("not-a-token") is None


# --- login -----------------------------------------------------------------------------


def test_login_returns_a_bearer_token():
    body = _login(_client()).json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 2 * 3600
    assert jwt.decode(
        body["access_token"], SECRET, algorithms=["HS256"], issuer="parkmind"
    )


@pytest.mark.parametrize(
    ("user", "password"),
    [("alice", "wrong"), ("nobody", "alice-pass-1"), ("ALICE", "alice-pass-1")],
)
def test_bad_credentials_get_the_same_401(user, password):
    r = _login(_client(), user, password)
    assert r.status_code == 401
    assert r.json() == {"detail": "Wrong username or password."}


def test_lockout_is_per_address_and_user_so_a_stranger_cannot_lock_alice_out():
    client = _client()
    for _ in range(5):
        client.post(
            "/api/login",
            json={"username": "alice", "password": "wrong"},
            headers={"X-Forwarded-For": "6.6.6.6"},
        )
    assert (
        _login(client).status_code == 429
    )  # same address in the test client: still locked
    other = _client()
    assert _login(other, "bob", "bob-pass-22").status_code == 200


def test_a_successful_login_clears_the_failures_of_that_user():
    client = _client()
    for _ in range(4):
        assert _login(client, "alice", "wrong").status_code == 401
    assert _login(client).status_code == 200
    for _ in range(4):
        assert _login(client, "alice", "wrong").status_code == 401


def test_repeated_failures_lock_the_login_even_for_the_right_password():
    client = _client()
    for _ in range(5):
        assert _login(client, "alice", "wrong").status_code == 401
    locked = _login(client)
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


# --- enforcement -----------------------------------------------------------------------

PROTECTED = [
    ("post", "/api/session", None),
    ("post", "/api/message?thread_id=t", {"text": "hi"}),
    (
        "post",
        "/api/constraints/confirm?thread_id=t",
        {"confirmed": True, "consent": True},
    ),
    ("get", "/api/proposal?thread_id=t", None),
    ("post", "/api/proposal/p1/approve?thread_id=t", None),
    ("get", "/api/plan?thread_id=t", None),
    ("post", "/api/events?thread_id=t", {"event_type": "ride_closure"}),
    ("post", "/api/events/confirm?thread_id=t", {"confirmed": True}),
]


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
def test_every_api_route_needs_a_token(method, path, body):
    client = _client()
    r = getattr(client, method)(path, **({"json": body} if body else {}))
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"
    bad = getattr(client, method)(
        path,
        headers={"Authorization": "Bearer nope"},
        **({"json": body} if body else {}),
    )
    assert bad.status_code == 401


def test_health_and_login_stay_public():
    client = _client()
    assert client.get("/api/health").status_code == 200
    assert _login(client).status_code == 200


def test_the_full_flow_works_with_a_token():
    client = _client()
    h = _bearer(client)
    t = {"thread_id": client.post("/api/session", headers=h).json()["thread_id"]}
    turn = client.post(
        "/api/message", params=t, headers=h, json={"text": FIRST_REQUEST}
    )
    assert turn.status_code == 200
    assert client.get("/api/plan", params=t, headers=h).status_code == 204


# --- ownership -------------------------------------------------------------------------


def test_a_session_is_invisible_to_other_users():
    client = _client()
    alice, bob = _bearer(client), _bearer(client, "bob", "bob-pass-22")
    t = {"thread_id": client.post("/api/session", headers=alice).json()["thread_id"]}
    assert client.get("/api/plan", params=t, headers=alice).status_code == 204
    for r in (
        client.get("/api/plan", params=t, headers=bob),
        client.get("/api/proposal", params=t, headers=bob),
        client.post("/api/message", params=t, headers=bob, json={"text": "hi"}),
    ):
        assert r.status_code == 404
    unknown = client.get("/api/plan", params={"thread_id": "nope"}, headers=bob)
    assert unknown.status_code == 404


# --- rate limits -----------------------------------------------------------------------


def _say(client, headers, t):
    return client.post("/api/message", params=t, headers=headers, json={"text": "hi"})


def _session(client, headers):
    return {
        "thread_id": client.post("/api/session", headers=headers).json()["thread_id"]
    }


def test_messages_are_limited_per_user_not_globally():
    client = _client()
    alice, bob = _bearer(client), _bearer(client, "bob", "bob-pass-22")
    t = _session(client, alice)
    codes = [_say(client, alice, t).status_code for _ in range(21)]
    assert codes[-1] == 429 and 429 not in codes[:20]
    assert _say(client, bob, _session(client, bob)).status_code != 429


def test_a_user_has_a_daily_llm_cap():
    client = _client(limits=Limits(messages_per_minute=100, messages_per_user_day=3))
    alice = _bearer(client)
    t = _session(client, alice)
    codes = [_say(client, alice, t).status_code for _ in range(4)]
    assert codes[-1] == 429 and 429 not in codes[:3]


def test_the_global_daily_cap_covers_all_users():
    limits = Limits(
        messages_per_minute=100, messages_per_user_day=100, messages_global_day=3
    )
    client = _client(limits=limits)
    alice, bob = _bearer(client), _bearer(client, "bob", "bob-pass-22")
    ta, tb = _session(client, alice), _session(client, bob)
    assert [_say(client, alice, ta).status_code != 429 for _ in range(2)] == [
        True,
        True,
    ]
    assert _say(client, bob, tb).status_code != 429
    assert _say(client, bob, tb).status_code == 429
    assert _say(client, alice, ta).status_code == 429


def test_every_graph_advancing_route_spends_the_budget():
    client = _client(limits=Limits(messages_per_minute=1))
    h = _bearer(client)
    t = _session(client, h)
    assert _say(client, h, t).status_code != 429
    for method, path, body in PROTECTED:
        if method == "post" and path.split("?")[0] != "/api/session":
            r = client.post(path.split("?")[0], params=t, headers=h, json=body)
            assert r.status_code == 429, path


def test_session_creation_is_limited():
    client = _client()
    h = _bearer(client)
    codes = [client.post("/api/session", headers=h).status_code for _ in range(21)]
    assert codes[:20] == [201] * 20
    assert codes[20] == 429


def test_a_full_global_cap_does_not_burn_user_quota():
    limits = Limits(
        messages_per_minute=100, messages_per_user_day=2, messages_global_day=1
    )
    client = _client(limits=limits)
    alice = _bearer(client)
    t = _session(client, alice)
    assert _say(client, alice, t).status_code != 429
    for _ in range(5):
        assert _say(client, alice, t).status_code == 429


def test_retry_after_reports_the_real_wait():
    limits = Limits(messages_per_minute=100, messages_per_user_day=1)
    client = _client(limits=limits)
    alice = _bearer(client)
    t = _session(client, alice)
    _say(client, alice, t)
    r = _say(client, alice, t)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 60


def test_the_limiter_forgets_idle_keys(monkeypatch):
    monkeypatch.setattr("parkmind.api.security._SWEEP_AT", 2)
    now = [0.0]
    limiter = RateLimiter(1, 10, clock=lambda: now[0])
    for i in range(5):
        limiter.hit(f"attacker-{i}")
    now[0] = 11.0
    limiter.hit("fresh")
    assert list(limiter._hits) == ["fresh"]


def test_the_limiter_window_slides():
    now = [0.0]
    limiter = RateLimiter(2, 10, clock=lambda: now[0])
    assert limiter.allow("k") and limiter.allow("k") and not limiter.allow("k")
    assert limiter.allow("other")
    now[0] = 10.0
    assert limiter.allow("k")


# --- headers & CORS --------------------------------------------------------------------


def test_security_headers_are_on_every_response_and_docs_keep_working():
    client = _client()
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert "Content-Security-Policy" not in client.get("/docs").headers


def test_cors_allows_the_authorization_header():
    rig = make_rig()
    app = create_app(
        rig.service,
        DescribePlanUseCase(factory_for(rig.deps)),
        env={},
        argv=[],
        cors_origins=["http://localhost:5173"],
        auth=_auth(),
    )
    r = TestClient(app).options(
        "/api/session",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


# --- server config is fail-closed ------------------------------------------------------


def _server():
    sys.path.insert(0, str(ROOT))
    import server

    return server


def test_the_server_refuses_to_start_without_credentials():
    server = _server()
    with pytest.raises(AuthConfigError):
        server.auth_from_env({})
    with pytest.raises(AuthConfigError):
        server.auth_from_env({"PARKMIND_AUTH_USERS": f"a={HASHES['alice']}"})
    with pytest.raises(AuthConfigError):
        server.auth_from_env({"PARKMIND_JWT_SECRET": SECRET})


@pytest.mark.parametrize("ttl", ["0", "-1", "abc", "inf"])
def test_a_bad_token_ttl_is_refused(ttl):
    server = _server()
    env = {
        "PARKMIND_AUTH_USERS": "alice=alice-pass-1",
        "PARKMIND_JWT_SECRET": SECRET,
        "PARKMIND_TOKEN_TTL_HOURS": ttl,
    }
    with pytest.raises(AuthConfigError):
        server.auth_from_env(env)


def test_bad_limit_values_are_refused():
    server = _server()
    for value in ("0", "-5", "many"):
        with pytest.raises(AuthConfigError):
            server.limits_from_env({"PARKMIND_MESSAGES_PER_USER_DAY": value})
    assert server.limits_from_env({}).messages_per_user_day == 300


def test_health_tells_the_ui_whether_to_ask_for_a_login():
    assert _client().get("/api/health").json()["auth_required"] is True


def test_the_server_builds_auth_from_env_and_can_be_opened_explicitly(capsys):
    server = _server()
    auth = server.auth_from_env(
        {
            "PARKMIND_AUTH_USERS": f"alice={HASHES['alice']}",
            "PARKMIND_JWT_SECRET": SECRET,
            "PARKMIND_TOKEN_TTL_HOURS": "1",
        }
    )
    assert auth.token_ttl_seconds == 3600 and auth.authenticate("alice", "alice-pass-1")
    assert server.auth_from_env({"PARKMIND_AUTH_DISABLED": "1"}) is None
    assert "WARNING" in capsys.readouterr().err


@pytest.mark.parametrize("switch", ["PARKMIND_PLAN_FROM_STORED", "E2E_NOW"])
def test_demo_switches_need_an_explicit_demo_opt_in(switch):
    server = _server()
    value = "10:00" if switch == "E2E_NOW" else "1"
    with pytest.raises(RuntimeError, match="PARKMIND_DEMO"):
        server.require_demo_for_switches({switch: value})
    server.require_demo_for_switches({switch: value, "PARKMIND_DEMO": "1"})
    server.require_demo_for_switches({})
