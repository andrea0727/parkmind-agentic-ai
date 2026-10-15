"""Authentication and rate limiting for ``parkmind-mcp`` over HTTP (guardrail G14).

stdio needs neither: the client launched the process. Over HTTP a planner tool
given a session id answers with plans that reflect that session's accessibility
requirements, so the server only leaves loopback when a bearer token is
configured, and every request must carry it.

The token comes from ``PARKMIND_MCP_TOKEN`` and has no default. A rejected
request is answered with a fixed structured error that never echoes anything
the caller sent (headers, path, body). Plain ASGI, so this module does not
import the MCP SDK.
"""

import hmac
import ipaddress
import json
import os
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

MCP_TOKEN_ENV = "PARKMIND_MCP_TOKEN"
MIN_TOKEN_LENGTH = 32

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_UNAUTHENTICATED = json.dumps(
    {
        "error": {
            "code": "UNAUTHENTICATED",
            "message": "A valid bearer token is required.",
            "retryable": False,
            "details": {},
        }
    }
).encode()


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def configured_token(env: MutableMapping[str, str] | None = None) -> str | None:
    """The configured bearer token, ``None`` when unset; a weak one is refused."""
    token = (os.environ if env is None else env).get(MCP_TOKEN_ENV, "").strip()
    if not token:
        return None
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(
            f"{MCP_TOKEN_ENV} must be at least {MIN_TOKEN_LENGTH} characters"
        )
    return token


def http_bind_error(host: str, token: str | None) -> str | None:
    """Why the server may not listen on ``host`` with this token, or ``None``."""
    if token is None and not is_loopback(host):
        return (
            f"--host {host} is not a loopback address; without {MCP_TOKEN_ENV} "
            "parkmind-mcp has no authentication, so it only serves this machine "
            "(127.0.0.1, ::1, localhost)"
        )
    return None


class BearerAuthMiddleware:
    """Answers 401 to every HTTP request that lacks the configured bearer token."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("a bearer token is required")
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if self._authorized(scope):
            await self._app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_UNAUTHENTICATED)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _UNAUTHENTICATED})

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                scheme, _, credentials = value.partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(
                    credentials.strip(), self._token
                )
        return False


class RateLimiter:
    """At most ``max_calls`` per ``per_seconds`` (sliding window), thread-safe."""

    def __init__(
        self,
        max_calls: int,
        per_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_calls < 1 or per_seconds <= 0:
            raise ValueError("max_calls and per_seconds must be positive")
        self._max = max_calls
        self._window = per_seconds
        self._clock = clock
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._calls and now - self._calls[0] >= self._window:
                self._calls.popleft()
            if len(self._calls) >= self._max:
                return False
            self._calls.append(now)
            return True
