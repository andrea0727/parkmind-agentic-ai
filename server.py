"""HTTP entrypoint (composition root): `python server.py` serves the API and the web UI on :8000.

ONE process, ONE worker: sessions (the checkpointer) and session-only data are in memory.
Env: PORT, PARKMIND_UI_DIR (built UI, default ui/web/dist), PARKMIND_CORS_ORIGINS (comma list),
ANTHROPIC_API_KEY (the LLM key),
PARKMIND_AUTH_USERS ("user=password,user2=password2") and PARKMIND_JWT_SECRET (>= 32 chars) are REQUIRED: the server refuses to start without them. PARKMIND_AUTH_DISABLED=1 opts out
(local demos only; every route is then open). PARKMIND_TOKEN_TTL_HOURS (default 2).
LLM caps (per user and global per 24 h): PARKMIND_MESSAGES_PER_USER_DAY (300), PARKMIND_MESSAGES_GLOBAL_DAY (2000).
PARKMIND_PLAN_FROM_STORED=1 (plan from the latest stored snapshot, no provider call: for demos
pinned to a snapshot taken during park hours), E2E_NOW=HH:MM (fixed park clock for demos).
"""

import os
import sys
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import uvicorn
from dotenv import load_dotenv

from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.api.app import create_app
from parkmind.api.guard import ensure_single_worker
from parkmind.api.security import AuthConfig, AuthConfigError, Limits, parse_users
from parkmind.core.contracts import PARK_TZ
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.graph.conversation import ConversationService
from parkmind.graph.initial_planning_graph import (
    build_initial_planning_graph,
    park_clock,
)
from parkmind.graph.replanning_graph import build_replanning_graph
from parkmind.services.use_cases.describe_plan import DescribePlanUseCase
from parkmind.services.use_cases.planning_deps import default_planning_deps

ROOT = Path(__file__).parent


def _deps_factory():
    if os.environ.get("PARKMIND_PLAN_FROM_STORED") != "1":
        return default_planning_deps
    from contextlib import contextmanager

    @contextmanager
    def stored_only():
        with default_planning_deps() as deps:
            yield replace(deps, collector=None)

    return stored_only


def _clock():
    fixed = os.environ.get("E2E_NOW")
    if not fixed:
        return park_clock
    hh, mm = fixed.split(":")

    def now() -> datetime:
        return datetime.combine(
            datetime.now(PARK_TZ).date(), time(int(hh), int(mm)), tzinfo=PARK_TZ
        )

    return now


def auth_from_env(env=os.environ) -> AuthConfig | None:
    """Fail closed: no credentials configured means no server, unless explicitly disabled."""
    if env.get("PARKMIND_AUTH_DISABLED") == "1":
        print("WARNING: PARKMIND_AUTH_DISABLED=1 - the API is open to anyone.", file=sys.stderr)
        return None
    users, secret = env.get("PARKMIND_AUTH_USERS", ""), env.get("PARKMIND_JWT_SECRET", "")
    if not users or not secret:
        raise AuthConfigError(
            "set PARKMIND_AUTH_USERS and PARKMIND_JWT_SECRET (see .env.example), "
            "or PARKMIND_AUTH_DISABLED=1 for a local demo"
        )
    hours = _positive(env, "PARKMIND_TOKEN_TTL_HOURS", "2", float)
    return AuthConfig(parse_users(users), secret, token_ttl_seconds=max(1, int(hours * 3600)))


def _positive(env, name: str, default: str, cast):
    try:
        value = cast(env.get(name, default))
    except ValueError:
        raise AuthConfigError(f"{name} must be a number") from None
    if not value > 0 or value == float("inf"):
        raise AuthConfigError(f"{name} must be greater than zero")
    return value


def limits_from_env(env=os.environ) -> Limits:
    return Limits(
        messages_per_user_day=_positive(env, "PARKMIND_MESSAGES_PER_USER_DAY", "300", int),
        messages_global_day=_positive(env, "PARKMIND_MESSAGES_GLOBAL_DAY", "2000", int),
    )


def build_app():
    load_dotenv()
    auth = auth_from_env()
    deps_factory = _deps_factory()
    clock = _clock()
    checkpointer = default_checkpointer()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required: set it in .env")
    extractor = AnthropicExtractor()
    initial = build_initial_planning_graph(
        extractor,
        checkpointer=checkpointer,
        deps_factory=deps_factory,
        clock=clock,
    )
    replanning = build_replanning_graph(
        checkpointer, deps_factory=deps_factory, clock=clock
    )
    ui_dir = Path(os.environ.get("PARKMIND_UI_DIR", ROOT / "ui" / "web" / "dist"))
    origins = [
        o
        for o in os.environ.get("PARKMIND_CORS_ORIGINS", "http://localhost:5173").split(
            ","
        )
        if o
    ]
    return create_app(
        ConversationService(initial, replanning, deps_factory=deps_factory),
        DescribePlanUseCase(deps_factory),
        clock=clock,
        ui_dir=ui_dir if ui_dir.is_dir() else None,
        cors_origins=origins,
        auth=auth,
        limits=limits_from_env(),
    )


if __name__ == "__main__":
    ensure_single_worker()
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
