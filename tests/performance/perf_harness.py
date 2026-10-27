"""An offline initial-planning graph that many threads can share, plus timing helpers."""

import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from elicit_support import FakeSessionStore, make_attraction, make_intake, make_names
from fakes import InMemorySnapshotRepository
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from planning_support import (
    NOW,
    PlanningAttractionRepository,
    factory_for,
    make_deps,
    seed_snapshot,
    snapshot_context,
)

from parkmind.agents.explain_agent import NarratorUnavailableError
from parkmind.core.contracts import Attraction
from parkmind.graph import initial_planning_graph as ipg
from parkmind.graph.checkpointing import default_checkpointer
from parkmind.services.use_cases.planning_deps import PlanningDeps

RIDES = {
    "TRON": "id-tron",
    "Space Mountain": "id-space",
    "Splash Mountain": "id-splash",
}

GROUNDED_TEXT = "Here is your plan, with the stops in the order you will visit them."


def label_for(index: int) -> str:
    """A guest label without digits (the schema drops labels that have any)."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    return f"Fam{letters[index // 26 % 26]}{letters[index % 26]}"


def journey_message(label: str, party: int, ride: str, hour: int = 8) -> str:
    return f"Family {label}: {party} adults. We want {ride}. We leave the park at {hour} PM."


def many_rides(count: int) -> list[Attraction]:
    return [
        make_attraction(f"id-ride-{n:02d}", f"Ride Number {n:02d}", wait=10)
        for n in range(count)
    ]


class ScenarioExtractor:
    """Derives the extraction from the guests' words; safe to share between threads."""

    def __init__(self, must_do_names: Sequence[str] | None = None) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self._must_do = list(must_do_names) if must_do_names is not None else None

    def extract(
        self, human_messages: Sequence[str], repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        with self._lock:
            self.calls += 1
        text = " ".join(human_messages)
        label = re.search(r"Family (\w+):", text)
        party = int(re.search(r"(\d+) adults", text).group(1))  # type: ignore[union-attr]
        hour = int(re.search(r"at (\d+) PM", text).group(1))  # type: ignore[union-attr]
        ride = re.search(r"We want ([A-Za-z ]+?)\.", text)
        must_do = self._must_do if self._must_do is not None else [ride.group(1)]  # type: ignore[union-attr]
        guests: list[dict[str, Any]] = [{"role": "adult"} for _ in range(party)]
        if label:
            guests[0]["label"] = label.group(1)
        return {
            "party_size": party,
            "guests": guests,
            "must_do": must_do,
            "departure_time": f"{hour + 12}:00",
        }


class CountingNarrator:
    """Counts calls and keeps every payload; replies with ``texts`` in order, then the last one."""

    def __init__(self, *texts: str) -> None:
        self._texts = list(texts) or [GROUNDED_TEXT]
        self._lock = threading.Lock()
        self.payloads: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.payloads)

    def narrate(
        self, payload_json: str, repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        with self._lock:
            self.payloads.append(payload_json)
            index = min(len(self.payloads), len(self._texts)) - 1
            return {"text": self._texts[index]}


class UnavailableNarrator(CountingNarrator):
    def narrate(
        self, payload_json: str, repair_hint: str | None = None
    ) -> Mapping[str, Any]:
        with self._lock:
            self.payloads.append(payload_json)
        raise NarratorUnavailableError("offline")


@dataclass
class Journey:
    graph: Any
    deps: PlanningDeps
    store: FakeSessionStore
    extractor: Any
    narrator: Any
    seen: dict[str, list[Any]] = field(default_factory=dict)

    def _config(self, thread_id: str) -> Any:
        return {"configurable": {"thread_id": thread_id}}

    def _record(self, thread_id: str, result: dict[str, Any]) -> dict[str, Any]:
        self.seen.setdefault(thread_id, []).append(result)
        return result

    def start(self, thread_id: str, message: str) -> dict[str, Any]:
        result = self.graph.invoke(
            {"thread_id": thread_id, "messages": [HumanMessage(content=message)]},
            config=self._config(thread_id),
        )
        return self._record(thread_id, result)

    def resume(self, thread_id: str, value: Any) -> dict[str, Any]:
        result = self.graph.invoke(
            Command(resume=value), config=self._config(thread_id)
        )
        return self._record(thread_id, result)

    def confirm(self, thread_id: str) -> dict[str, Any]:
        return self.resume(thread_id, {"confirmed": True, "consent": True})

    def approve(self, thread_id: str) -> dict[str, Any]:
        return self.resume(thread_id, {"decision": "APPROVED"})

    def run(self, thread_id: str, message: str) -> dict[str, Any]:
        self.start(thread_id, message)
        self.confirm(thread_id)
        return self.approve(thread_id)

    def values(self, thread_id: str) -> dict[str, Any]:
        snapshot = self.graph.get_state(self._config(thread_id), subgraphs=True)
        for task in snapshot.tasks:
            if task.state is not None and hasattr(task.state, "values"):
                return dict(task.state.values)
        return dict(snapshot.values)

    def active_plan(self, thread_id: str) -> Any:
        return self.deps.plans.get_active(thread_id)

    def proposals(self) -> list[tuple[str, Any]]:
        return list(self.deps.proposals.rows.values())


def interrupt_payload(result: Mapping[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__")
    return dict(interrupts[0].value) if interrupts else None


def build_journey(
    *,
    extractor: Any = None,
    narrator: Any = None,
    attractions: Sequence[Attraction] | None = None,
) -> Journey:
    intake, store = make_intake()
    snapshots = InMemorySnapshotRepository()
    seed_snapshot(
        snapshots,
        snapshot_context(attractions, retrieved_at=NOW - timedelta(minutes=5)),
    )
    repository = (
        PlanningAttractionRepository(attractions) if attractions is not None else None
    )
    deps = make_deps(sessions=store, snapshots=snapshots, attractions=repository)
    extractor = extractor or ScenarioExtractor()
    narrator = narrator or UnavailableNarrator()
    graph = ipg.build_initial_planning_graph(
        extractor,
        intake,
        default_checkpointer(),
        deps_factory=factory_for(deps),
        clock=lambda: NOW,
        names=make_names(attractions),
        narrator=narrator,
    )
    return Journey(graph, deps, store, extractor, narrator)


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile, so a p95 of 20 runs is the slowest but one."""
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def timed(action: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = action()
    return result, time.perf_counter() - started
