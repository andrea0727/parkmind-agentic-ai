"""Bearer auth, bind rule and rate limit of parkmind-mcp over HTTP (guardrail G14)."""

import asyncio
import json
from typing import Any

import pytest

from parkmind.tools.mcp_auth import (
    MCP_TOKEN_ENV,
    BearerAuthMiddleware,
    RateLimiter,
    configured_token,
    http_bind_error,
)

TOKEN = "t" * 40


async def _call(
    token: str, headers: list[tuple[bytes, bytes]]
) -> tuple[list[dict[str, Any]], list[str]]:
    sent: list[dict[str, Any]] = []
    reached: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await BearerAuthMiddleware(app, token)(
        {"type": "http", "path": "/mcp", "headers": headers}, receive, send
    )
    return sent, reached


def _status(sent: list[dict[str, Any]]) -> int:
    return int(sent[0]["status"])


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Bearer wrong-token-wrong-token-wrong-token-xx")],
        [(b"authorization", TOKEN.encode())],
        [(b"authorization", f"Basic {TOKEN}".encode())],
        [(b"authorization", b"Bearer ")],
        [(b"x-api-key", TOKEN.encode())],
    ],
)
def test_a_request_without_the_valid_token_is_refused_and_echoes_nothing(
    headers: list[tuple[bytes, bytes]],
) -> None:
    sent, reached = asyncio.run(_call(TOKEN, headers))

    assert reached == []
    assert _status(sent) == 401
    body = sent[1]["body"]
    assert json.loads(body)["error"]["code"] == "UNAUTHENTICATED"
    for _, value in headers:
        assert value not in body
    assert (b"www-authenticate", b"Bearer") in sent[0]["headers"]


def test_non_http_scopes_pass_through() -> None:
    seen: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    asyncio.run(BearerAuthMiddleware(app, TOKEN)({"type": "lifespan"}, None, None))  # type: ignore[arg-type]

    assert seen == ["lifespan"]


def test_the_middleware_needs_a_token() -> None:
    with pytest.raises(ValueError):
        BearerAuthMiddleware(lambda *a: None, "")  # type: ignore[arg-type, return-value]


# --- where the server may listen ------------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", "::"])
def test_a_non_loopback_host_without_a_token_is_refused(host: str) -> None:
    problem = http_bind_error(host, None)

    assert problem is not None and "not a loopback address" in problem


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_needs_no_token(host: str) -> None:
    assert http_bind_error(host, None) is None


def test_a_token_lifts_the_loopback_refusal() -> None:
    assert http_bind_error("0.0.0.0", TOKEN) is None


def test_the_token_comes_from_the_environment_and_has_no_default() -> None:
    assert configured_token({}) is None
    assert configured_token({MCP_TOKEN_ENV: "  "}) is None
    assert configured_token({MCP_TOKEN_ENV: f" {TOKEN} "}) == TOKEN


def test_a_short_token_is_refused() -> None:
    with pytest.raises(ValueError, match=MCP_TOKEN_ENV):
        configured_token({MCP_TOKEN_ENV: "short"})


# --- rate limit -----------------------------------------------------------------------


def test_the_limiter_allows_max_calls_per_window_then_recovers() -> None:
    now = [0.0]
    limiter = RateLimiter(2, per_seconds=60, clock=lambda: now[0])

    assert [limiter.allow() for _ in range(3)] == [True, True, False]

    now[0] = 59.0
    assert limiter.allow() is False
    now[0] = 60.0
    assert limiter.allow() is True


def test_the_limiter_rejects_a_non_positive_setting() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)
