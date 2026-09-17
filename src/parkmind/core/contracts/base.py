"""
Base models and shared validators for ParkMind domain contracts.

Handles:
- Timezone validation (Invariant #6: all datetimes are America/New_York)
- Bounded numeric validation
- Shared types used across multiple contracts
"""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, field_validator, model_validator, Field
from zoneinfo import ZoneInfo

# Park timezone (reference: Magic Kingdom, Walt Disney World)
PARK_TZ = ZoneInfo("America/New_York")


def _check_datetime_aware(value: Any, path: str = "") -> None:
    """Recursively check that all datetime objects are timezone-aware."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"{path}: datetime must be timezone-aware (America/New_York)"
            )
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_datetime_aware(v, f"{path}[{k!r}]" if path else str(k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_datetime_aware(v, f"{path}[{i}]" if path else f"[{i}]")


class ParkMindBaseModel(BaseModel):
    """Base model ensuring timezone awareness for all datetime fields."""

    @model_validator(mode="after")
    def validate_all_datetimes_aware(self) -> "ParkMindBaseModel":
        """All datetime fields must be timezone-aware in America/New_York, including nested in collections."""
        for field_name, field_value in self:
            _check_datetime_aware(field_value, field_name)
        return self


class TimeWindow(ParkMindBaseModel):
    """
    Time interval for a fixed event (lunch, show, departure).

    All timestamps are timezone-aware, park-local.
    Window must be valid: end > start.

    §33 / Invariant #6
    """

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def check_window(self) -> "TimeWindow":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class WaitEstimate(ParkMindBaseModel):
    """Wait time data for an attraction at a point in time."""

    attraction_id: str
    wait_minutes: float = Field(ge=0)
    status: str  # AttractionStatus (avoid circular import)


class WeatherHour(ParkMindBaseModel):
    """Weather forecast for a one-hour interval."""

    timestamp: datetime
    condition: str  # e.g., "clear", "rain", "storm"
    temperature_f: float
    precipitation_probability: float = Field(ge=0, le=1)


class AccessibilityCheck(ParkMindBaseModel):
    """Result of checking an attraction against a guest's accessibility requirements."""

    attraction_id: str
    guest_id: str
    eligible: bool
    conflicting_requirement: str | None  # RideRestriction if not eligible
    provenance: dict[str, Any]  # source, version, timestamp


class ToolCall(ParkMindBaseModel):
    """Record of a tool invocation by the agentic context loader."""

    tool_name: str  # data.get_waits, knowledge.check_accessibility, etc.
    query: str | dict[str, Any]
    iteration: int
    result_count: int


class CoverageReport(ParkMindBaseModel):
    """
    Report of data coverage after agentic context loading.

    Drives fallback to deterministic loader if incomplete.
    §8.1 [C11]
    """

    required_attractions_covered: bool
    required_shows_covered: bool
    weather_covered: bool
    accessibility_checks_complete: bool  # for each guest + each attraction
    coverage_gaps: list[str] = Field(default_factory=list)  # what's missing


class CheckResult(ParkMindBaseModel):
    """
    Result of ConstraintChecker validation.

    Governs whether a plan passes the gate or triggers re-solve.
    §20
    """

    valid: bool
    violations: list[dict[str, Any]] = Field(default_factory=list)
    # Each violation: {rule, message, stop_id, suggestion}


class PlanDiff(ParkMindBaseModel):
    """
    Structured diff between base_plan and candidate_plan.

    Used for explanation and human approval.
    """

    stops_added: list[str] = Field(default_factory=list)
    stops_removed: list[str] = Field(default_factory=list)
    stops_reordered: list[str] = Field(default_factory=list)
    stops_modified: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # stops_modified[stop_id] = {field_name: (old_value, new_value)}


class HardConstraintSet(ParkMindBaseModel):
    """
    Union of hard constraints across all guests and party rules.

    Used in GroupObjective to track what the planner must satisfy.
    §11, §20
    """

    must_do: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    party_walking_budget_minutes: int | None = None
    per_guest_daily_walking_limits: dict[str, int] = Field(
        default_factory=dict
    )  # guest_id -> minutes
    per_guest_rest_frequency: dict[str, int] = Field(
        default_factory=dict
    )  # guest_id -> minutes between rests
    height_constraints: dict[str, float] = Field(
        default_factory=dict
    )  # guest_id -> min height in cm
    ride_restrictions: dict[str, list[str]] = Field(
        default_factory=dict
    )  # guest_id -> list[RideRestriction]


class EventThresholds(ParkMindBaseModel):
    """
    Per-event decision thresholds derived from the most-sensitive guest.

    Used by EventPolicy to determine requires_replan.
    Thresholds are profile-aware, not static.
    §11, §25
    """

    queue_spike_minutes: float = Field(
        ge=0, default=20
    )  # wait increase to trigger MEDIUM severity
    walking_overrun_minutes: float = Field(
        ge=0, default=10
    )  # minutes over daily limit
    fatigue_threshold: float = Field(
        ge=0, le=1, default=0.7
    )  # confidence level for GUEST_FATIGUE to require replan
