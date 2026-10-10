"""Shared plumbing for the real-ports e2e runner: env loading, NVIDIA extractor, call spies."""

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv

load_dotenv(REPO / ".env")

from langchain_core.messages import HumanMessage, SystemMessage

from parkmind.agents.elicit_agent import ExtractorOutputError
from parkmind.agents.elicit_prompt import ELICIT_SYSTEM_PROMPT
from parkmind.agents.elicit_schema import ElicitationExtraction
from parkmind.core.llm import get_llm
from parkmind.graph import initial_planning_graph as ipg

RAW_DIR = Path(os.environ.get("E2E_RAW_DIR", REPO / "docs" / "e2e" / "raw"))
CONFIRM = {"confirmed": True, "consent": True}

LLM_CALLS: list[dict[str, Any]] = []
EVENTS: list[str] = []


class NvidiaExtractor:
    def __init__(self) -> None:
        self._llm = get_llm().bind_tools([ElicitationExtraction], tool_choice=ElicitationExtraction.__name__)

    def extract(self, human_messages: Sequence[str], repair_hint: str | None = None) -> Mapping[str, Any]:
        listing = "\n".join(f"{i}. {t}" for i, t in enumerate(human_messages, start=1))
        content = f"Guest messages, oldest first:\n{listing}"
        if repair_hint:
            content += f"\n\nYour previous output was rejected by validation. Fix exactly:\n{repair_hint}"
        response = self._llm.invoke([SystemMessage(ELICIT_SYSTEM_PROMPT), HumanMessage(content)])
        calls = getattr(response, "tool_calls", None) or []
        LLM_CALLS.append({"hint": repair_hint, "calls": calls})
        if not calls:
            raise ExtractorOutputError("no tool call")
        return calls[0]["args"]


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
        (ipg.ExplainPlanUseCase, "execute", "explain"),
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
