"""``planner.*`` tools over MCP (P0-27 Done-when), offline on the 2026-09-27 capture.

Also the P0-24 guarantees that matter most for the planner: no tool writes a
plan or a proposal, and accessibility crosses the boundary by reference only.
"""

import json
from datetime import timedelta
from typing import Any

import factories
from mcp_support import NOW, Deps, call_tool, list_tools
from planning_support import factory_for

from parkmind.core.contracts import (
    MobilityRequirement,
    PreferenceSource,
    RideRestriction,
    RuleId,
)
from parkmind.services.use_cases.planner_queries import PlannerQueries
from parkmind.tools.registry import build_registry

PLANNER_TOOLS = {
    "planner.build_plan",
    "planner.check_plan",
    "planner.score_preferences",
    "planner.forecast_waits",
}
SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"
WRITES = {"save", "activate", "resolve", "supersede_pending"}


def _constraints(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "party_size": 2,
        "guests": [
            factories.guest(guest_id="g1"),
            factories.guest(guest_id="g2", height_cm=168.0),
        ],
        "must_do": [SPACE_MOUNTAIN],
        "departure_time": NOW.replace(hour=17, minute=30, second=0),
    }
    fields.update(overrides)
    return factories.party_constraints(**fields)


def _args(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "session_id": "s1",
        "constraints": _constraints().model_dump(mode="json"),
        "now": NOW.isoformat(),
    }
    arguments.update(overrides)
    return arguments


def _ok(result: Any) -> dict[str, Any]:
    assert result.is_error is False, result.content[0].text
    return result.structured_content


def test_planner_tools_have_typed_schemas() -> None:
    tools = {t.name: t for t in list_tools(Deps().registry())}

    assert PLANNER_TOOLS <= set(tools)
    for name in PLANNER_TOOLS:
        schema = tools[name].input_schema
        assert schema["type"] == "object", name
        assert set(tools[name].output_schema["properties"]) == {"data", "provenance"}, (
            name
        )
        # accessibility only by reference: no field that could carry requirements
        assert not {"accessibility", "requirements"} & set(schema["properties"]), name


def test_build_plan_tool_matches_use_case() -> None:
    """Delegation: the tool's plan is the use cases' plan, stop for stop."""
    deps = Deps()

    payload = _ok(call_tool(deps.registry(), "planner.build_plan", _args()))
    direct = PlannerQueries(factory_for(deps.value)).build_plan(
        session_id="s1", constraints=_constraints(), profiles=[], guest_ids=[], now=NOW
    )

    tool_stops = [
        (s["node_id"], s["kind"], s["arrival_time"])
        for s in payload["data"]["plan"]["stops"]
    ]
    direct_stops = [
        (s.node_id, s.kind.value, s.arrival_time.isoformat()) for s in direct.plan.stops
    ]
    assert tool_stops == direct_stops
    assert payload["data"]["check"]["valid"] is True
    assert payload["data"]["resolved"] is True
    provenance = payload["provenance"]
    assert provenance["source"] == "parkmind_planner"
    assert provenance["snapshot_id"] == deps.collected.snapshot_id  # type: ignore[union-attr]
    assert (
        provenance["strategy"]
        == payload["data"]["plan"]["provenance"]["optimizer_strategy"]
    )


def test_end_to_end_build_then_check_over_session() -> None:
    registry = Deps().registry()

    plan = _ok(call_tool(registry, "planner.build_plan", _args()))["data"]["plan"]
    check = _ok(call_tool(registry, "planner.check_plan", _args(plan=plan)))

    assert check["data"]["check"] == {"valid": True, "violations": []}
    assert check["provenance"]["strategy"] == "constraint_checker"


def test_check_plan_returns_rule_ids_and_repair_hints() -> None:
    registry = Deps().registry()
    plan = _ok(call_tool(registry, "planner.build_plan", _args()))["data"]["plan"]
    first_ride = next(s["node_id"] for s in plan["stops"] if s["kind"] == "ATTRACTION")
    avoiding = _constraints(avoid=[first_ride]).model_dump(mode="json")

    check = _ok(
        call_tool(
            registry, "planner.check_plan", _args(plan=plan, constraints=avoiding)
        )
    )

    violations = check["data"]["check"]["violations"]
    assert check["data"]["check"]["valid"] is False
    avoid = next(v for v in violations if v["rule"] == RuleId.AVOID.value)
    assert avoid["stop_id"] == first_ride
    assert avoid["suggestion"]


def test_score_preferences_reports_weight_provenance() -> None:
    stated = factories.guest_profile(
        guest_id="g1",
        queue_tolerance=factories.preference(0.2, source=PreferenceSource.STATED),
    )
    learned = factories.guest_profile(
        guest_id="g2",
        walking_tolerance=factories.preference(
            0.3, source=PreferenceSource.LEARNED, stated_value=None
        ),
    )
    arguments = _args(
        profiles=[stated.model_dump(mode="json"), learned.model_dump(mode="json")],
        top=3,
    )

    data = _ok(call_tool(Deps().registry(), "planner.score_preferences", arguments))[
        "data"
    ]

    assert set(data["weight_provenance"].values()) <= {"stated", "learned", "default"}
    assert data["weight_provenance"]  # every weight says where it came from
    assert set(data["weights"]) == set(data["weight_provenance"])
    assert len(data["top_attractions"]) == 3
    assert set(data["per_guest_top"]) == {"g1", "g2"}
    assert data["model_version"]


def test_forecast_waits_reports_strategy() -> None:
    later = NOW + timedelta(hours=2)
    payload = _ok(
        call_tool(
            Deps().registry(),
            "planner.forecast_waits",
            {
                "attraction_ids": [SPACE_MOUNTAIN, "no-such"],
                "at": [NOW.isoformat(), later.isoformat()],
            },
        )
    )

    forecasts = payload["data"]["forecasts"]
    assert [f["attraction_id"] for f in forecasts] == [SPACE_MOUNTAIN, SPACE_MOUNTAIN]
    assert {f["strategy"] for f in forecasts} == {"api_forecast"}
    assert all(f["snapshot_id"] for f in forecasts)
    assert payload["data"]["unknown_ids"] == ["no-such"]
    assert payload["provenance"]["strategy"] == "api_forecast"


def _store_requirements(deps: Deps) -> None:
    deps.sessions.put(
        "s1",
        factories.accessibility(
            guest_id="g2",
            mobility_requirements=[MobilityRequirement.WHEELCHAIR],
            ride_restrictions=[RideRestriction.NOT_RECOMMENDED_EXPECTANT],
            daily_walking_limit_minutes=7,
            rest_frequency_minutes=45,
            consent=True,
            retention_policy="session_only",
        ),
    )


def test_accessibility_never_in_tool_payloads() -> None:
    """C19: requirements are read server-side; no value comes back out, even in a violation."""
    deps = Deps()
    _store_requirements(deps)
    registry = deps.registry()
    planning = _args(
        guest_ids=["g2"], constraints=_constraints(must_do=[]).model_dump(mode="json")
    )

    built = _ok(call_tool(registry, "planner.build_plan", planning))
    responses = [
        built,
        _ok(
            call_tool(
                registry,
                "planner.check_plan",
                {**planning, "plan": built["data"]["plan"]},
            )
        ),
        _ok(call_tool(registry, "planner.score_preferences", planning)),
    ]

    text = json.dumps(responses)
    for value in (
        "WHEELCHAIR",
        "EXPECTANT",
        "rest frequency",
        "walking total",
        "heat-sensitive",
    ):
        assert value not in text, value
    for violation in built["data"]["check"]["violations"]:
        if violation["rule"] in {"ACCESSIBILITY", "RIDE_RESTRICTION"}:
            assert "details stay in the guest's session" in violation["message"]


def test_session_only_requirements_fail_closed_out_of_process() -> None:
    """Requirements on file in another process are missing here: the check fails closed."""
    deps = (
        Deps()
    )  # an empty session store: the guest's session_only record is elsewhere

    data = _ok(
        call_tool(deps.registry(), "planner.build_plan", _args(guest_ids=["g2"]))
    )["data"]

    assert data["check"]["valid"] is False
    assert RuleId.DATA_FRESHNESS.value in {
        v["rule"] for v in data["check"]["violations"]
    }


class _Recording:
    """Wraps a repository and records every method called on it."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if callable(attribute):

            def recorded(*args: Any, **kwargs: Any) -> Any:
                self.calls.append(name)
                return attribute(*args, **kwargs)

            return recorded
        return attribute


def test_no_tool_writes_plans_or_proposals() -> None:
    """Section 27 [C23]: no MCP tool can activate or mutate a plan -- every tool, once."""
    deps = Deps()
    plans, proposals = _Recording(deps.plans), _Recording(deps.proposals)
    deps.value = deps.value.__class__(
        **{**deps.value.__dict__, "plans": plans, "proposals": proposals}
    )
    registry = deps.registry()
    plan = _ok(call_tool(registry, "planner.build_plan", _args()))["data"]["plan"]
    arguments = {
        "planner.build_plan": _args(),
        "planner.check_plan": _args(plan=plan),
        "planner.score_preferences": _args(),
        "planner.forecast_waits": {"attraction_ids": [SPACE_MOUNTAIN]},
        "data.get_walking_time": {
            "origin_node_id": SPACE_MOUNTAIN,
            "destination_node_id": BIG_THUNDER,
        },
        "knowledge.search_policies": {"query": "Rider Switch"},
        "knowledge.find_similar_attractions": {"attraction_id": BIG_THUNDER},
        "knowledge.check_accessibility": {"attraction_ids": [BIG_THUNDER]},
    }

    names = build_registry().names()
    for name in names:
        _ok(call_tool(registry, name, arguments.get(name)))

    assert len(names) == 14
    assert not set(plans.calls) & WRITES, plans.calls
    assert not set(proposals.calls) & WRITES, proposals.calls
