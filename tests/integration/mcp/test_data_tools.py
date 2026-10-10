"""``data.*`` tools over MCP on the real 2026-09-27 capture (P0-25 Done-when).

Every call is a real MCP round trip to ``parkmind-mcp`` in process; the ports
behind it are the capture collected by the real ``SnapshotCollector``, the Magic
Kingdom catalog and the strict ``RoutingClient`` (``mcp_support``). Offline: no
network, no Postgres, no MCP server running beforehand.
"""

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from capture import capture
from mcp_support import NOW, Deps, call_tool, list_tools

from parkmind.services.clients.routing_client import RoutingClient

DATA_TOOLS = {
    "data.get_live_waits",
    "data.get_attraction_status",
    "data.get_schedule",
    "data.get_showtimes",
    "data.get_weather",
    "data.get_walking_time",
    "data.get_attraction_info",
}
SNAPSHOT_TOOLS = {
    "data.get_live_waits": "themeparks_wiki",
    "data.get_attraction_status": "themeparks_wiki",
    "data.get_showtimes": "themeparks_wiki",
    "data.get_weather": "open_meteo",
}

SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
TRON = "5a43d1a7-ad53-4d25-abfe-25625f0da304"
HUB = "90d79335-c907-4069-a021-d0fe1ec73ae2"
BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"

# Keys of the provider payloads (ThemeParks /live, Open-Meteo /forecast) that
# must never appear in an MCP response.
PROVIDER_KEYS = {
    "liveData",
    "entityType",
    "queue",
    "STANDBY",
    "waitTime",
    "lastUpdated",
    "startTime",
    "endTime",
    "temperature_2m",
    "weather_code",
    "hourly",
}


@pytest.fixture(scope="module")
def deps() -> Deps:
    return Deps()


def _ok(result: Any) -> dict[str, Any]:
    assert result.is_error is False, result.content[0].text
    return result.structured_content


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_data_tools_have_typed_schemas(deps: Deps) -> None:
    tools = {t.name: t for t in list_tools(deps.registry())}

    assert DATA_TOOLS <= set(tools)
    for name in DATA_TOOLS:
        tool = tools[name]
        assert tool.input_schema["type"] == "object", name
        assert set(tool.output_schema["properties"]) == {"data", "provenance"}, name
        assert tool.annotations.read_only_hint is True, name
        assert tool.description, name


def test_snapshot_tools_carry_provenance(deps: Deps) -> None:
    assert deps.collected is not None
    for name, source in SNAPSHOT_TOOLS.items():
        provenance = _ok(call_tool(deps.registry(), name))["provenance"]

        assert provenance["source"] == source, name
        assert provenance["snapshot_id"] == deps.collected.snapshot_id, name
        assert provenance["strategy"] == "snapshot", name
        assert provenance["degraded"] is None, name


def test_live_responses_carry_snapshot_id_and_age(deps: Deps) -> None:
    ten_minutes_later = deps.registry(clock=lambda: NOW + timedelta(minutes=10))
    two_hours_later = deps.registry(clock=lambda: NOW + timedelta(hours=2))

    fresh = _ok(call_tool(ten_minutes_later, "data.get_live_waits"))["provenance"]
    old = _ok(call_tool(two_hours_later, "data.get_live_waits"))["provenance"]

    assert deps.collected is not None
    assert fresh["snapshot_id"] == old["snapshot_id"] == deps.collected.snapshot_id
    assert fresh["age_seconds"] == 600.0 and fresh["stale"] is False
    assert old["age_seconds"] == 7200.0 and old["stale"] is True


def test_a_configured_collector_answers_from_a_fresh_collection() -> None:
    live = Deps(collect_at=None, live_collector=True)
    later = NOW + timedelta(hours=3)

    provenance = _ok(
        call_tool(live.registry(clock=lambda: later), "data.get_live_waits")
    )["provenance"]

    assert provenance["strategy"] == "live"
    assert provenance["age_seconds"] == 0.0 and provenance["stale"] is False


def test_responses_hold_contract_fields_only(deps: Deps) -> None:
    arguments = {
        "data.get_walking_time": {"origin_node_id": HUB, "destination_node_id": TRON}
    }
    for name in sorted(DATA_TOOLS):
        payload = _ok(call_tool(deps.registry(), name, arguments.get(name)))

        leaked = _keys(payload) & PROVIDER_KEYS
        assert leaked == set(), f"{name} leaks provider fields {leaked}"
        json.dumps(payload)  # JSON-native all the way down


def test_get_live_waits_reports_unknown_ids_instead_of_failing(deps: Deps) -> None:
    data = _ok(
        call_tool(
            deps.registry(),
            "data.get_live_waits",
            {"attraction_ids": [SPACE_MOUNTAIN, "no-such"]},
        )
    )["data"]

    assert [w["attraction_id"] for w in data["waits"]] == [SPACE_MOUNTAIN]
    assert data["waits"][0]["status"] == "OPERATING"
    assert data["unknown_ids"] == ["no-such"]


def _raw_show_times(kind: str) -> dict[str, set[datetime]]:
    times: dict[str, set[datetime]] = {}
    for entity in capture("themeparks_live.json")["liveData"]:
        for slot in entity.get("showtimes") or []:
            if slot.get("type") == kind and slot.get("startTime"):
                times.setdefault(entity["id"], set()).add(
                    datetime.fromisoformat(slot["startTime"])
                )
    return times


def test_get_showtimes_excludes_operating_and_ticketed_events(deps: Deps) -> None:
    """The capture has 22 Performance Time, 18 Special Ticketed Event and 7 Operating slots."""
    performances = _raw_show_times("Performance Time")
    excluded = _raw_show_times("Special Ticketed Event")
    for show, windows in _raw_show_times("Operating").items():
        excluded.setdefault(show, set()).update(windows)
    assert excluded, "the capture should hold slots that are not performances"

    showtimes = _ok(call_tool(deps.registry(), "data.get_showtimes"))["data"][
        "showtimes"
    ]

    assert showtimes, "the capture has scheduled performances"
    for show, starts in showtimes.items():
        returned = {datetime.fromisoformat(s) for s in starts}
        assert returned <= performances.get(show, set()), show
        assert not returned & excluded.get(show, set()), show


def test_walking_time_marks_curated_estimated_unknown() -> None:
    curated = Deps(routing=RoutingClient(custom_matrix={(SPACE_MOUNTAIN, TRON): 4.0}))
    lenient = Deps(routing=RoutingClient(fallback_enabled=True))

    def walk(deps: Deps, origin: str, destination: str) -> dict[str, Any]:
        arguments = {"origin_node_id": origin, "destination_node_id": destination}
        return _ok(call_tool(deps.registry(), "data.get_walking_time", arguments))

    assert walk(curated, TRON, TRON)["data"] == {"minutes": 0.0, "basis": "identity"}
    assert walk(curated, TRON, SPACE_MOUNTAIN)["data"] == {
        "minutes": 4.0,
        "basis": "curated",
    }
    estimated = walk(curated, HUB, BIG_THUNDER)
    assert (
        estimated["data"]["basis"] == "estimated" and estimated["data"]["minutes"] > 0
    )
    assert estimated["provenance"]["strategy"] == "estimated"
    unknown = walk(lenient, HUB, "no-such-node")["data"]
    assert unknown == {
        "minutes": None,
        "basis": "unknown",
    }  # never the 10-minute fallback


def test_get_weather_returns_the_hours_overlapping_the_window(deps: Deps) -> None:
    start = NOW  # 11:02 -- the 11:00 hour is still current
    end = NOW.replace(hour=14, minute=0, second=0)

    hours = _ok(
        call_tool(
            deps.registry(),
            "data.get_weather",
            {"start": start.isoformat(), "end": end.isoformat()},
        )
    )["data"]["hours"]

    stamps = [datetime.fromisoformat(h["timestamp"]).hour for h in hours]
    assert stamps == [11, 12, 13, 14]


def test_get_weather_rejects_an_inverted_window(deps: Deps) -> None:
    result = call_tool(
        deps.registry(),
        "data.get_weather",
        {"start": NOW.isoformat(), "end": (NOW - timedelta(hours=1)).isoformat()},
    )

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "INVALID_ARGUMENT"


def test_get_schedule_returns_the_operating_window(deps: Deps) -> None:
    park = _ok(call_tool(deps.registry(), "data.get_schedule"))["data"]["park"]

    assert datetime.fromisoformat(park["opening_time"]).hour == 9
    assert datetime.fromisoformat(park["closing_time"]).hour == 18


def test_get_schedule_without_a_schedule_is_unavailable(deps: Deps) -> None:
    result = call_tool(
        deps.registry(), "data.get_schedule", {"service_date": "2026-12-25"}
    )

    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "UNAVAILABLE" and error["retryable"] is True


def test_get_attraction_info_returns_catalog_metadata(deps: Deps) -> None:
    data = _ok(
        call_tool(
            deps.registry(),
            "data.get_attraction_info",
            {"attraction_ids": [SPACE_MOUNTAIN, "x"]},
        )
    )["data"]

    (space,) = data["attractions"]
    assert space["node_id"] == SPACE_MOUNTAIN
    assert space["height_restriction_cm"] is not None
    assert {"category", "land", "outdoor", "typical_wait_minutes"} <= set(space)
    assert data["unknown_ids"] == ["x"]


def test_no_snapshot_at_all_is_unavailable() -> None:
    empty = Deps(collect_at=None)

    result = call_tool(empty.registry(), "data.get_live_waits")

    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "UNAVAILABLE" and error["retryable"] is True
    assert "snapshot" in error["message"]
