"""API security: password hashing, signed access tokens, rate limiting, security headers.

Stdlib scrypt for passwords and PyJWT (HS256) for tokens. Nothing here knows about planning.
"""

import base64
import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import jwt

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_ALGORITHM = "HS256"
_ISSUER = "parkmind"
MIN_SECRET_LENGTH = 32
_SWEEP_AT = 10_000


class AuthConfigError(ValueError):
    """The auth settings are unusable; the server must not start with them."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


# Verified against when the username is unknown, so a miss costs the same as a wrong password.
_DECOY_HASH = hash_password(secrets.token_urlsafe(16))


MIN_PASSWORD_LENGTH = 8


def _is_wellformed_hash(stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        base64.b64decode(salt, validate=True)
        base64.b64decode(digest, validate=True)
        return scheme == "scrypt" and min(int(n), int(r), int(p)) > 0
    except ValueError:
        return False


def parse_users(raw: str) -> dict[str, str]:
    """``name=password,name2=password2``; a value may instead be a ``scrypt$`` hash.

    Plain passwords are hashed once here, so only hashes are held and compared at runtime.
    Passwords cannot contain a comma.
    """
    users: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, secret = entry.partition("=")
        name = name.strip()
        if not sep or not name or not secret:
            raise AuthConfigError(
                "PARKMIND_AUTH_USERS must be 'user=password,user2=password2'"
            )
        if secret.startswith("scrypt$"):
            if not _is_wellformed_hash(secret):
                raise AuthConfigError(f"the scrypt hash of {name!r} is malformed")
            users[name] = secret
        elif len(secret) < MIN_PASSWORD_LENGTH:
            raise AuthConfigError(
                f"the password of {name!r} must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        else:
            users[name] = hash_password(secret)
    if not users:
        raise AuthConfigError("PARKMIND_AUTH_USERS defines no users")
    return users


@dataclass(frozen=True)
class AuthConfig:
    users: Mapping[str, str]
    secret: str
    token_ttl_seconds: int = 2 * 3600
    clock: Callable[[], float] = field(default=time.time, repr=False)

    def __post_init__(self) -> None:
        if len(self.secret) < MIN_SECRET_LENGTH:
            raise AuthConfigError(
                f"PARKMIND_JWT_SECRET must be at least {MIN_SECRET_LENGTH} characters"
            )
        if not self.users:
            raise AuthConfigError("at least one user is required")

    def authenticate(self, username: str, password: str) -> bool:
        stored = self.users.get(username)
        ok = verify_password(password, stored if stored is not None else _DECOY_HASH)
        return ok and stored is not None

    def issue_token(self, username: str) -> str:
        now = int(self.clock())
        return jwt.encode(
            {
                "sub": username,
                "iss": _ISSUER,
                "iat": now,
                "exp": now + self.token_ttl_seconds,
            },
            self.secret,
            algorithm=_ALGORITHM,
        )

    def verify_token(self, token: str) -> str | None:
        """The username a valid, unexpired token was issued to; None otherwise."""
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=[_ALGORITHM],
                issuer=_ISSUER,
                options={"require": ["sub", "exp", "iss"], "verify_exp": False},
            )
        except jwt.PyJWTError:
            return None
        exp = claims.get("exp")
        if not isinstance(exp, int | float) or exp <= self.clock():
            return None
        sub = claims.get("sub")
        return sub if isinstance(sub, str) and sub in self.users else None


class RateLimiter:
    """Sliding window: at most ``limit`` hits per ``window`` seconds for each key."""

    def __init__(
        self,
        limit: int,
        window: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> deque[float] | None:
        hits = self._hits.get(key)
        if hits is None:
            return None
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if not hits:
            del self._hits[key]
            return None
        return hits

    def _sweep(self, now: float) -> None:
        if len(self._hits) > _SWEEP_AT:
            for key in list(self._hits):
                self._live(key, now)

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            self._sweep(now)
            hits = self._live(key, now)
            if hits is not None and len(hits) >= self._limit:
                return False
            self._hits.setdefault(key, deque()).append(now)
            return True

    def check(self, key: str) -> bool:
        """Whether ``key`` is under the limit, without recording a hit."""
        with self._lock:
            hits = self._live(key, self._clock())
            return hits is None or len(hits) < self._limit

    def hit(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            self._sweep(now)
            self._hits.setdefault(key, deque()).append(now)

    def release(self, key: str) -> None:
        """Take back the most recent hit (a reservation that turned out not to count)."""
        with self._lock:
            hits = self._hits.get(key)
            if hits:
                hits.pop()
                if not hits:
                    del self._hits[key]

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def retry_after(self, key: str) -> int:
        """Whole seconds until ``key`` has room again (at least 1)."""
        now = self._clock()
        with self._lock:
            hits = self._live(key, now)
            if hits is None or len(hits) < self._limit:
                return 1
            return max(1, math.ceil(hits[0] + self._window - now))


@dataclass(frozen=True)
class Limits:
    """Caps that protect the LLM key. A day is a sliding 24 hours."""

    messages_per_minute: int = 20
    messages_per_user_day: int = 300
    messages_global_day: int = 2000
    sessions_per_hour: int = 20


SECURITY_HEADERS: Mapping[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}

# The UI loads only its own bundle plus Google Fonts; Swagger (/docs) is exempt in the app.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
