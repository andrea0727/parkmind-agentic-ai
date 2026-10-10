"""LOAD CONTEXT through parkmind-mcp (P0-24 Done-when: "The context loader can be
configured to call data.* / knowledge.* through the MCP client, with in-process
fallback through the same port" [C23]).

The MCP path is a real MCP session to the server in process; the direct path
is LOAD CONTEXT reading the snapshot itself. Both run on the 2026-09-27 capture.
"""

import json
from datetime import datetime
from typing import Any

import factories
import pytest
from mcp_support import NOW, Deps, capture_park, mk_catalog
from planning_support import factory_for

from parkmind.config.settings import settings
from parkmind.core.contracts import MobilityRequirement, RideRestriction
from parkmind.services.clients.mcp.context_client import McpContextData
from parkmind.services.ports import FromSnapshotRef, SnapshotRef
from parkmind.services.use_cases.load_context import LoadContextUseCase, LoadedContext
from parkmind.services.use_cases.planner_queries import PlannerQueries
from parkmind.services.use_cases.planning_deps import ContextUnavailableError
from parkmind.services.use_cases.tool_context import (
    InProcessContextData,
    configured_context_data,
)
from parkmind.tools.mcp_server import create_server

SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
UNREACHABLE = "http://127.0.0.1:9/mcp"  # nothing listens on the discard port


def _party(deps: Deps) -> Deps:
    deps.sessions.put(
        "s1",
        factories.accessibility(
            guest_id="g2",
            mobility_requirements=[MobilityRequirement.WHEELCHAIR],
            ride_restrictions=[RideRestriction.NOT_RECOMMENDED_EXPECTANT],
            consent=True,
            retention_policy="session_only",
        ),
    )
    return deps


def _direct(deps: Deps) -> LoadedContext:
    return LoadContextUseCase(factory_for(deps.value)).execute("s1", ["g2"], NOW)


def _through(deps: Deps, port: Any) -> LoadedContext:
    return LoadContextUseCase(factory_for(deps.value), context_data=port).execute(
        "s1", ["g2"], NOW
    )


def _mcp(deps: Deps, server: Any = None) -> McpContextData:
    return McpContextData(
        server if server is not None else create_server(deps.registry())
    )


def _catalog_view(loaded: LoadedContext) -> dict[str, Any]:
    """What planning reads: the catalog's waits, statuses, showtimes; weather in park hours."""
    live = loaded.live_context
    ids = {a.node_id for a in mk_catalog()}
    park = capture_park()
    return {
        "snapshot": (live.snapshot_id, live.retrieved_at, loaded.source),
        "waits": {k: v for k, v in live.waits.items() if k in ids},
        "statuses": {k: v for k, v in live.statuses.items() if k in ids},
        "showtimes": {k: sorted(v) for k, v in live.showtimes.items() if k in ids},
        "weather": [
            h
            for h in live.weather
            if h.timestamp < park.closing_time
            and h.timestamp.hour >= park.opening_time.hour
        ],
        "coverage": (
            live.coverage.required_attractions_covered,
            live.coverage.required_shows_covered,
            live.coverage.weather_covered,
            live.coverage.accessibility_checks_complete,
        ),
        "checks": sorted(
            (c.guest_id, c.attraction_id, c.eligible, c.conflicting_requirement)
            for c in live.accessibility_results
        ),
    }


def test_mcp_context_equals_in_process_context() -> None:
    deps = _party(Deps())

    direct = _direct(deps)
    over_mcp = _through(deps, _mcp(deps))

    assert direct.transport == "in_process"
    assert over_mcp.transport == "mcp"
    assert _catalog_view(over_mcp) == _catalog_view(direct)
    assert _catalog_view(over_mcp)["checks"], "the party's accessibility was checked"
    tools = [call.tool_name for call in over_mcp.live_context.tool_trace]
    assert "mcp:data.get_live_waits" in tools
    assert "mcp:knowledge.check_accessibility" in tools


def test_the_tool_trace_carries_no_accessibility_values() -> None:
    """The trace is checkpointed with the context [C19]: names and counts only."""
    deps = _party(Deps())

    trace = _through(deps, _mcp(deps)).for_state().tool_trace

    text = json.dumps([call.model_dump(mode="json") for call in trace])
    for value in ("WHEELCHAIR", "EXPECTANT", "TRANSFER", "g2"):
        assert value not in text, value


def test_server_down_falls_back_in_process() -> None:
    deps = _party(Deps())

    fallback = _through(deps, McpContextData(UNREACHABLE, timeout_seconds=3))

    assert fallback.transport == "in_process_fallback"
    assert _catalog_view(fallback) == _catalog_view(_direct(deps))
    assert all(
        c.tool_name.startswith("in_process:") for c in fallback.live_context.tool_trace
    )


class _TwoSnapshots(InProcessContextData):
    """A transport whose waits come from another snapshot than the rest."""

    transport = "mcp"

    def live_waits(self) -> FromSnapshotRef:  # type: ignore[type-arg]
        answer = super().live_waits()
        other = SnapshotRef("snap_other", answer.snapshot.retrieved_at, "snapshot")
        return FromSnapshotRef(answer.value, other)


def test_a_context_stitched_from_two_snapshots_is_refused() -> None:
    deps = _party(Deps())

    loaded = _through(deps, _TwoSnapshots(factory_for(deps.value), clock=lambda: NOW))

    assert (
        loaded.transport == "in_process_fallback"
    )  # refused, then answered in-process
    assert loaded.live_context.snapshot_id != "snap_other"


def test_no_snapshot_is_a_domain_answer_not_a_transport_failure() -> None:
    empty = Deps(collect_at=None)

    with pytest.raises(ContextUnavailableError, match="snapshot"):
        _through(empty, _mcp(empty))


def test_the_setting_selects_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CONTEXT_TRANSPORT", "in_process")
    assert configured_context_data() is None

    monkeypatch.setattr(settings, "CONTEXT_TRANSPORT", "mcp")
    monkeypatch.setattr(settings, "MCP_URL", "http://127.0.0.1:8765/mcp")
    port = configured_context_data()
    assert isinstance(port, McpContextData)


def test_plans_are_identical_over_mcp() -> None:
    """The planner reaches the same plan whether LOAD CONTEXT read over MCP or in-process."""
    deps = Deps()
    constraints = factories.party_constraints(
        party_size=2,
        guests=[
            factories.guest(guest_id="g1"),
            factories.guest(guest_id="g2", height_cm=168.0),
        ],
        must_do=[SPACE_MOUNTAIN],
        departure_time=NOW.replace(hour=17, minute=30, second=0),
    )

    def plan_stops(port: Any) -> list[tuple[str, str, datetime]]:
        candidate = PlannerQueries(
            factory_for(deps.value), context_data=port
        ).build_plan(
            session_id="s1", constraints=constraints, profiles=[], guest_ids=[], now=NOW
        )
        assert candidate.check.valid
        return [(s.node_id, s.kind.value, s.arrival_time) for s in candidate.plan.stops]

    in_process = plan_stops(None)
    over_mcp = plan_stops(_mcp(deps))

    assert over_mcp == in_process


def test_the_planner_behind_the_tools_never_reads_over_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with PARKMIND_CONTEXT_TRANSPORT=mcp: planner.* is served by the boundary itself."""
    monkeypatch.setattr(settings, "CONTEXT_TRANSPORT", "mcp")
    deps = Deps()

    loaded = PlannerQueries(factory_for(deps.value))._load.execute("s1", [], NOW)

    assert loaded.transport == "in_process"
    assert LoadContextUseCase(factory_for(deps.value))._context_data is not None
