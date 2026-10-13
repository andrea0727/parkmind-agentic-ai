"""HTTP entrypoint (composition root): `python server.py` serves the API and the web UI on :8000.

ONE process, ONE worker: sessions (the checkpointer) and session-only data are in memory.
Env: PORT, PARKMIND_UI_DIR (built UI, default ui/web/dist), PARKMIND_CORS_ORIGINS (comma list),
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

from parkmind.agents.nvidia_extractor import NvidiaExtractor
from parkmind.api.app import create_app
from parkmind.api.guard import ensure_single_worker
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


def build_app():
    load_dotenv()
    deps_factory = _deps_factory()
    clock = _clock()
    checkpointer = default_checkpointer()
    initial = build_initial_planning_graph(
        NvidiaExtractor(),
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
    )


if __name__ == "__main__":
    ensure_single_worker()
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
