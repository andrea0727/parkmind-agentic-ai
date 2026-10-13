"""``planner.*`` tools over MCP (P0-27 Done-when), offline on the 2026-09-27 capture.

Also the P0-24 guarantees that matter most for the planner: no tool writes a
plan or a proposal, and accessibility crosses the boundary by reference only.
"""

import json
from datetime import datetime, timedelta
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
from parkmind.tools.planner_tools import MAX_FORECASTS, MAX_GUESTS, MAX_STOPS
from parkmind.tools.registry import build_registry

PLANNER_TOOLS = {
    "planner.build_plan",
    "planner.check_plan",
    "planner.replan",
    "planner.score_preferences",
    "planner.forecast_waits",
}
SPACE_MOUNTAIN = "b2260923-9315-40fd-9c6b-44dd811dbe64"
BIG_THUNDER = "de3309ca-97d5-4211-bffe-739fed47e92f"
WRITES = {"save", "activate", "resolve", "supersede_pending", "record"}


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
        _ok(
            call_tool(
                registry,
                "planner.replan",
                {**planning, "plan": built["data"]["plan"]},
            )
        ),
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
    events = _Recording(deps.value.events)
    states = _Recording(deps.value.execution_states)
    deps.value = deps.value.__class__(
        **{
            **deps.value.__dict__,
            "plans": plans,
            "proposals": proposals,
            "events": events,
            "execution_states": states,
        }
    )
    registry = deps.registry()
    plan = _ok(call_tool(registry, "planner.build_plan", _args()))["data"]["plan"]
    arguments = {
        "planner.build_plan": _args(),
        "planner.check_plan": _args(plan=plan),
        "planner.replan": _args(plan=plan),
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

    assert len(names) == 15
    for repository in (plans, proposals, events, states):
        assert not set(repository.calls) & WRITES, repository.calls


def _rejected(result: Any) -> list[str]:
    """The fields an INVALID_ARGUMENT answer names."""
    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    return error["details"]["fields"]


def test_planner_inputs_are_capped() -> None:
    registry = Deps(collect_at=None).registry()
    many_guests = [f"g{i}" for i in range(MAX_GUESTS + 1)]
    crowd = _constraints(
        party_size=MAX_GUESTS + 1,
        guests=[factories.guest(guest_id=g) for g in many_guests],
    )
    plan = _ok(call_tool(Deps().registry(), "planner.build_plan", _args()))["data"][
        "plan"
    ]
    long_plan = {**plan, "stops": plan["stops"][:1] * (MAX_STOPS + 1)}

    assert _rejected(
        call_tool(registry, "planner.build_plan", _args(guest_ids=many_guests))
    ) == ["guest_ids"]
    assert _rejected(
        call_tool(
            registry,
            "planner.score_preferences",
            _args(constraints=crowd.model_dump(mode="json")),
        )
    ) == ["constraints"]
    assert _rejected(
        call_tool(registry, "planner.check_plan", _args(plan=long_plan))
    ) == ["plan"]


def test_forecast_waits_caps_attractions_times_arrivals() -> None:
    times = [(NOW + timedelta(minutes=10 * i)).isoformat() for i in range(48)]
    ids = [f"attraction-{i}" for i in range(MAX_FORECASTS // 48 + 1)]

    result = call_tool(
        Deps(collect_at=None).registry(),
        "planner.forecast_waits",
        {"attraction_ids": ids, "at": times},
    )

    assert _rejected(result) == ["at"]


def test_forecast_waits_without_times_is_capped_by_the_attractions() -> None:
    """Guards ``attraction_ids``' own cap of 100, not the 500-forecast total.

    With ``at`` empty the total equals the attraction count, which the field cap
    keeps far below 500, so the total check never runs here; it is covered by
    ``test_forecast_waits_caps_attractions_times_arrivals``.
    """
    ids = [f"attraction-{i}" for i in range(MAX_FORECASTS + 1)]

    result = call_tool(
        Deps(collect_at=None).registry(),
        "planner.forecast_waits",
        {"attraction_ids": ids},
    )

    assert _rejected(result) == ["attraction_ids"]


def test_forecast_waits_honors_the_callers_now() -> None:
    """Like the other planner tools: set ``now`` to replay a day."""
    payload = _ok(
        call_tool(
            Deps().registry(clock=lambda: NOW + timedelta(days=30)),
            "planner.forecast_waits",
            {"attraction_ids": [SPACE_MOUNTAIN], "now": NOW.isoformat()},
        )
    )

    (forecast,) = payload["data"]["forecasts"]  # a month later there is no snapshot
    assert datetime.fromisoformat(forecast["at"]) == NOW
    assert forecast["strategy"] == "api_forecast"


def test_an_unknown_session_is_answered_like_a_guest_without_records() -> None:
    """A structured, fail-closed answer that tells an unknown session from a known one
    no better than the caller's own ids do; never an error or an internal message."""
    deps = Deps()
    _store_requirements(deps)  # session s1 holds g2's requirements
    registry = deps.registry()

    def rules(session_id: str, guest_id: str) -> set[str]:
        data = _ok(
            call_tool(
                registry,
                "planner.check_plan",
                _args(session_id=session_id, guest_ids=[guest_id], plan=plan),
            )
        )["data"]
        assert data["check"]["valid"] is False
        return {v["rule"] for v in data["check"]["violations"]}

    plan = _ok(call_tool(registry, "planner.build_plan", _args()))["data"]["plan"]

    assert rules("no-such-session", "g2") == rules("s1", "g9")
    assert RuleId.DATA_FRESHNESS.value in rules("no-such-session", "g2")


# -- planner.replan (section 31) -------------------------------------------------------

REPLAN_AT = NOW + timedelta(minutes=20)
"""Inside the capture's freshness window, so the context needs no reload."""


def _built_plan(registry: Any) -> dict[str, Any]:
    return _ok(call_tool(registry, "planner.build_plan", _args()))["data"]["plan"]


def _replan(registry: Any, plan: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return _ok(
        call_tool(
            registry,
            "planner.replan",
            _args(plan=plan, now=REPLAN_AT.isoformat(), **overrides),
        )
    )


def _rides(plan: dict[str, Any], *, after: datetime | None = None) -> list[str]:
    return [
        s["node_id"]
        for s in plan["stops"]
        if s["kind"] == "ATTRACTION"
        and (after is None or datetime.fromisoformat(s["arrival_time"]) >= after)
    ]


def _down(attraction_id: str) -> dict[str, Any]:
    return factories.event(
        event_id="guest-report-1",
        attraction_id=attraction_id,
        timestamp=REPLAN_AT - timedelta(minutes=1),
    ).model_dump(mode="json")


def test_replan_end_to_end_avoids_a_closure_sent_inline() -> None:
    """build_plan -> replan: the ride reported down leaves the new horizon."""
    deps = Deps()
    registry = deps.registry()
    plan = _built_plan(registry)
    closed = next(r for r in _rides(plan, after=REPLAN_AT) if r != SPACE_MOUNTAIN)

    payload = _replan(registry, plan, events=[_down(closed)])

    data = payload["data"]
    assert data["valid"] is True, data["failure"]
    assert data["plan"]["plan_id"] != plan["plan_id"]
    assert closed not in _rides(data["plan"], after=REPLAN_AT)
    assert closed in data["diff"]["stops_removed"]
    assert data["check"]["valid"] is True
    provenance = payload["provenance"]
    assert provenance["source"] == "parkmind_planner"
    assert provenance["snapshot_id"] == deps.collected.snapshot_id  # type: ignore[union-attr]
    assert provenance["strategy"].startswith("replanner+")
    assert provenance["age_seconds"] is not None


def test_replan_keeps_the_progress_sent_and_starts_after_it() -> None:
    registry = Deps().registry()
    plan = _built_plan(registry)
    done = [s["node_id"] for s in plan["stops"][:2]]
    progress = factories.execution_state(
        plan_id=plan["plan_id"],
        completed_stop_ids=done,
        as_of=REPLAN_AT,
        walking_minutes_consumed={},
        remaining_walking_cap_minutes={},
    ).model_dump(mode="json")

    data = _replan(registry, plan, execution_state=progress)["data"]

    assert data["valid"] is True, data["failure"]
    assert data["execution_state"]["completed_stop_ids"] == done
    stops = data["plan"]["stops"]
    assert [s["node_id"] for s in stops[:2]] == done
    assert all(
        datetime.fromisoformat(s["arrival_time"]) >= REPLAN_AT for s in stops[2:]
    )


def test_replan_with_rest_first_opens_the_new_horizon_with_a_rest() -> None:
    registry = Deps().registry()
    plan = _built_plan(registry)

    data = _replan(registry, plan, rest_first=True)["data"]

    assert data["valid"] is True, data["failure"]
    done = set(data["execution_state"]["completed_stop_ids"])
    horizon = [s for s in data["plan"]["stops"] if s["node_id"] not in done]
    assert horizon[0]["kind"] == "REST"


def test_replan_progress_for_another_plan_is_an_invalid_argument() -> None:
    registry = Deps().registry()
    plan = _built_plan(registry)
    other = factories.execution_state(plan_id="another_plan").model_dump(mode="json")

    result = call_tool(
        registry,
        "planner.replan",
        _args(plan=plan, now=REPLAN_AT.isoformat(), execution_state=other),
    )

    assert "execution_state" in _rejected(result)


def test_a_tight_walking_limit_shortens_the_day_instead_of_failing() -> None:
    """A 7-minute daily walking limit for a guest on file: the horizon is built to
    fit what is left (the optimizer's walking budget), not failed after the fact."""
    deps = Deps()
    registry = deps.registry()
    plan = _built_plan(registry)
    _store_requirements(deps)

    data = _replan(registry, plan, guest_ids=["g2"])["data"]

    assert data["valid"] is True, data["failure"]
    assert data["plan"]["total_walking_minutes"] <= 7
    assert data["check"]["valid"] is True


def test_an_infeasible_replan_says_why_without_the_guests_limits() -> None:
    """The guest already walked 10 of their 7 daily minutes: no horizon fits, and
    the failure keeps its reason but not the limit [C19]."""
    deps = Deps()
    registry = deps.registry()
    plan = _built_plan(registry)
    _store_requirements(deps)
    progress = factories.execution_state(
        plan_id=plan["plan_id"],
        completed_stop_ids=[plan["stops"][0]["node_id"]],
        as_of=REPLAN_AT,
        walking_minutes_consumed={"g2": 10.0},
        remaining_walking_cap_minutes={},
    ).model_dump(mode="json")

    data = _replan(registry, plan, guest_ids=["g2"], execution_state=progress)["data"]

    assert data["valid"] is False and data["plan"] is None
    assert "details stay in the guest's session" in data["failure"]
    for value in ("walking total", "limit of 7", "rest frequency"):
        assert value not in data["failure"], value
