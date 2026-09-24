"""Valid §33 contract instances for repository tests.

Every builder takes keyword overrides, so a test states only what it cares
about. Times are fixed park-local datetimes: no ``now()``, fully reproducible.
"""

from datetime import datetime, timedelta
from typing import Any

from parkmind.core.contracts import (
    PARK_TZ,
    AccessibilityRequirements,
    ApprovalStatus,
    Attraction,
    AttractionCategory,
    AttractionStatus,
    BehaviorEntry,
    BehaviorEventType,
    CoverageReport,
    DataSource,
    Event,
    EventSource,
    EventType,
    Guest,
    GuestProfile,
    GuestRole,
    LiveContext,
    MobilityRequirement,
    Park,
    Plan,
    PlanDiff,
    PlanExecutionState,
    PlanningPace,
    PlanningStyle,
    PreferenceSource,
    PreferenceValue,
    Proposal,
    Provenance,
    RideRestriction,
    Stop,
    StopKind,
    WaitEstimate,
    WeatherHour,
)

NOW = datetime(2026, 9, 16, 10, 0, tzinfo=PARK_TZ)


def guest(**overrides: Any) -> Guest:
    fields: dict[str, Any] = {"guest_id": "g1", "role": GuestRole.ADULT, "height_cm": 175.0}
    return Guest(**{**fields, **overrides})


def preference(value: float = 0.5, **overrides: Any) -> PreferenceValue:
    fields: dict[str, Any] = {
        "value": value,
        "source": PreferenceSource.STATED,
        "confidence": 0.8,
        "updated_at": NOW,
        "stated_value": value,
    }
    return PreferenceValue(**{**fields, **overrides})


def guest_profile(**overrides: Any) -> GuestProfile:
    fields: dict[str, Any] = {
        "guest_id": "g1",
        "pace": PlanningPace.BALANCED,
        "queue_tolerance": preference(0.4),
        "walking_tolerance": preference(0.6),
        "planning_style": PlanningStyle.FLEXIBLE,
        "profile_version": 1,
    }
    return GuestProfile(**{**fields, **overrides})


def accessibility(**overrides: Any) -> AccessibilityRequirements:
    fields: dict[str, Any] = {
        "guest_id": "g1",
        "daily_walking_limit_minutes": 90,
        "mobility_requirements": [MobilityRequirement.WHEELCHAIR],
        "ride_restrictions": [RideRestriction.NOT_RECOMMENDED_HIGH_G_FORCE],
        "consent": True,
        "retention_policy": "persisted",
    }
    return AccessibilityRequirements(**{**fields, **overrides})


def behavior_entry(**overrides: Any) -> BehaviorEntry:
    fields: dict[str, Any] = {
        "entry_id": "e1",
        "event_type": BehaviorEventType.PROPOSAL_ACCEPTED,
        "proposal_id": "prop_1",
        "plan_id": "plan_1",
        "decision": "ACCEPTED",
        "attraction_ids": ["a1"],
        "timestamp": NOW,
    }
    return BehaviorEntry(**{**fields, **overrides})


def provenance(**overrides: Any) -> Provenance:
    fields: dict[str, Any] = {
        "snapshot_id": "snap_1",
        "retrieved_at": NOW,
        "data_sources": [DataSource.THEMEPARKS_WIKI, DataSource.OPEN_METEO],
        "forecast_strategy": "current_wait",
        "optimizer_strategy": "greedy_repair",
        "constraints_version": 1,
        "objective_version": "obj_1",
        "preference_model_version": "pref_1",
        "prompt_versions": {"elicit": "v1"},
        "weight_provenance": {"queue": PreferenceSource.STATED},
    }
    return Provenance(**{**fields, **overrides})


def stop(**overrides: Any) -> Stop:
    fields: dict[str, Any] = {
        "node_id": "a1",
        "kind": StopKind.ATTRACTION,
        "arrival_time": NOW,
        "departure_time": NOW + timedelta(minutes=30),
        "expected_wait_minutes": 20.0,
        "walking_minutes": 5.0,
        "utility": 1.5,
        "served_guests": ["g1"],
    }
    return Stop(**{**fields, **overrides})


def plan(**overrides: Any) -> Plan:
    fields: dict[str, Any] = {
        "plan_id": "plan_1",
        "version": 1,
        "stops": [stop()],
        "total_wait_minutes": 20.0,
        "total_walking_minutes": 5.0,
        "objective_value": 1.5,
        "per_guest_satisfaction": {"g1": 0.9},
        "provenance": provenance(),
    }
    return Plan(**{**fields, **overrides})


def proposal(**overrides: Any) -> Proposal:
    fields: dict[str, Any] = {
        "proposal_id": "prop_1",
        "base_plan_id": "plan_0",
        "candidate_plan_id": "plan_1",
        "reason": "initial plan",
        "diff": PlanDiff(),
        "explanation": "Fits the party's limits.",
        "approval_status": ApprovalStatus.PENDING,
        "provenance": provenance(),
    }
    return Proposal(**{**fields, **overrides})


def execution_state(**overrides: Any) -> PlanExecutionState:
    fields: dict[str, Any] = {
        "plan_id": "plan_1",
        "completed_stop_ids": ["a1"],
        "as_of": NOW,
        "walking_minutes_consumed": {"g1": 12.0},
        "remaining_walking_cap_minutes": {"g1": 78.0},
    }
    return PlanExecutionState(**{**fields, **overrides})


def event(**overrides: Any) -> Event:
    fields: dict[str, Any] = {
        "event_id": "ev_1",
        "type": EventType.ATTRACTION_DOWN,
        "source": EventSource.MONITOR,
        "attraction_id": "a1",
        "confidence": 1.0,
        "timestamp": NOW,
    }
    return Event(**{**fields, **overrides})


def live_context(**overrides: Any) -> LiveContext:
    fields: dict[str, Any] = {
        "snapshot_id": "snap_1",
        "retrieved_at": NOW,
        "waits": {
            "a1": WaitEstimate(
                attraction_id="a1", wait_minutes=25.0, status=AttractionStatus.OPERATING
            )
        },
        "statuses": {"a1": AttractionStatus.OPERATING},
        "showtimes": {"a1": [NOW + timedelta(hours=2)]},
        "weather": [
            WeatherHour(
                timestamp=NOW,
                condition="clear",
                temperature_f=84.0,
                precipitation_probability=0.1,
            )
        ],
        "coverage": CoverageReport(
            required_attractions_covered=True,
            required_shows_covered=True,
            weather_covered=True,
            accessibility_checks_complete=True,
        ),
    }
    return LiveContext(**{**fields, **overrides})


def attraction(**overrides: Any) -> Attraction:
    fields: dict[str, Any] = {
        "node_id": "a1",
        "name": "Space Mountain",
        "category": AttractionCategory.THRILL,
        "height_restriction_cm": 112,
        "typical_wait_minutes": 60,
        "outdoor": False,
    }
    return Attraction(**{**fields, **overrides})


def park(**overrides: Any) -> Park:
    fields: dict[str, Any] = {
        "park_id": "mk",
        "name": "Magic Kingdom Park",
        "opening_time": datetime(2026, 9, 16, 9, 0, tzinfo=PARK_TZ),
        "closing_time": datetime(2026, 9, 16, 22, 0, tzinfo=PARK_TZ),
        "outdoor": True,
    }
    return Park(**{**fields, **overrides})
