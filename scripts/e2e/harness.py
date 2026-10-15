"""Shared plumbing for the real-ports e2e runner: env loading, Anthropic extractor, call spies."""

import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv

load_dotenv(REPO / ".env")

from parkmind.agents.elicit_agent import AnthropicExtractor
from parkmind.core.contracts import PARK_TZ
from parkmind.graph import initial_planning_graph as ipg

RAW_DIR = Path(os.environ.get("E2E_RAW_DIR", REPO / "docs" / "e2e" / "raw"))
CONFIRM = {"confirmed": True, "consent": True}

def e2e_now() -> datetime:
    """Fixed park-time clock (E2E_NOW=HH:MM, default 10:00) so runs never depend on the time of day."""
    hh, mm = os.environ.get("E2E_NOW", "10:00").split(":")
    return datetime.combine(datetime.now(PARK_TZ).date(), time(int(hh), int(mm)), tzinfo=PARK_TZ)


LLM_CALLS: list[dict[str, Any]] = []
EVENTS: list[str] = []


class RecordingExtractor:
    """The production Anthropic extractor, recording each call for the report."""

    def __init__(self) -> None:
        self._inner = AnthropicExtractor()

    def extract(self, human_messages: Sequence[str], repair_hint: str | None = None) -> Mapping[str, Any]:
        try:
            args = self._inner.extract(human_messages, repair_hint)
        except Exception as exc:
            LLM_CALLS.append({"hint": repair_hint, "calls": [], "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
            raise
        LLM_CALLS.append({"hint": repair_hint, "calls": [{"args": args}]})
        return args


def spy(cls: Any, method: str, label: str) -> None:
    original = getattr(cls, method)

    def wrapper(this: Any, *a: Any, **k: Any) -> Any:
        EVENTS.append(label)
        return original(this, *a, **k)

    setattr(cls, method, wrapper)


def install_stage_spies() -> None:
    for cls, meth, label in (
        (ipg.LoadContextUseCase, "execute", "load_context"),
        (ipg.BuildPlanUseCase, "resolve_group", "resolve_group"),
        (ipg.BuildPlanUseCase, "build", "build_plan"),
        (ipg.CheckPlanUseCase, "execute", "check"),
        (ipg.ExplainPlanUseCase, "prepare", "explain"),
        (ipg.ProposePlanUseCase, "execute", "propose"),
        (ipg.ResolveProposalUseCase, "execute", "resolve_proposal"),
    ):
        spy(cls, meth, label)


def graph_state(graph: Any, config: Any) -> dict[str, Any]:
    snap = graph.get_state(config, subgraphs=True)
    for t in snap.tasks:
        if t.state is not None and hasattr(t.state, "values"):
            return t.state.values
    return snap.values
