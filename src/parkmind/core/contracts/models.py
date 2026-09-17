"""
ParkMind domain contracts — Pydantic v2 models.

Core aggregates for guest modeling, planning, and orchestration.
All timestamps are timezone-aware (America/New_York).

§33 Domain Contracts — v2.2 [C12, C13, C15, C18, C19]
"""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

from .base import (
    ParkMindBaseModel,
    TimeWindow,
    CoverageReport,
    ToolCall,
    CheckResult,
    PlanDiff,
    HardConstraintSet,
    EventThresholds,
)
from .enums import (
    AttractionStatus,
    StopKind,
    SensitivityKind,
    SensitivityLevel,
    AttractionCategory,
    MobilityRequirement,
    RideRestriction,
    EventSource,
    BehaviorEventType,
    RejectionReason,
    EventSeverity,
    EventType,
    ApprovalStatus,
    PreferenceSource,
    GuestRole,
    PlanningPace,
    PlanningStyle,
    DataSource,
)


# ============================================================================
# IDENTITY & PROFILE
# ============================================================================


class Guest(ParkMindBaseModel):
    """
    Guest identity — facts only, not preferences.

    Height is a safety input to Constraint Rule 2.
    Per guest, not a dictionary on the party.

    §33 / §12 [C15]
    """

    guest_id: str
    role: GuestRole
    height_cm: float | None = Field(default=None, ge=50, le=250)


class PreferenceValue(ParkMindBaseModel):
    """
    Wraps a single preference dimension with provenance.

    Tracks stated values separately from learned values.
    Confidence indicates how much evidence supports the current value.

    - For tolerances (queue, walking): value is 0–1
    - For affinities (thematic): value is −1..1

    §33 / §10 [C12]
    """

    value: float
    source: PreferenceSource
    confidence: float = Field(ge=0, le=1)
    updated_at: datetime
    stated_value: float | None = None  # Verbatim from guest; never overwritten by learning

    @model_validator(mode="after")
    def check_stated_value_retention(self) -> "PreferenceValue":
        """
        If source is "stated", stated_value must not be None.
        If source is "learned" or "default", stated_value may be retained separately.

        Invariant #3: stated_value is never overwritten by learning.
        """
        if self.source == PreferenceSource.STATED and self.stated_value is None:
            raise ValueError("stated_value must be provided when source is 'stated'")
        return self


class GuestProfile(ParkMindBaseModel):
    """
    Guest preferences — soft, learnable constraints.

    Separate from AccessibilityRequirements (hard constraints).
    One per guest; keyed by guest_id.

    §10, §33 [C12, C15]
    """

    guest_id: str
    pace: PlanningPace
    queue_tolerance: PreferenceValue
    walking_tolerance: PreferenceValue  # Comfort, not a limit. Limit is in AccessibilityRequirements.
    sensitivities: dict[SensitivityKind, SensitivityLevel] = Field(
        default_factory=dict
    )
    thematic_affinity: dict[str, PreferenceValue] = Field(
        default_factory=dict
    )  # theme/land -> PreferenceValue
    preferred_categories: list[AttractionCategory] = Field(default_factory=list)
    avoided_categories: list[AttractionCategory] = Field(default_factory=list)
    planning_style: PlanningStyle
    profile_version: int

    @field_validator("queue_tolerance", "walking_tolerance")
    @classmethod
    def validate_tolerance_bounds(cls, v: PreferenceValue) -> PreferenceValue:
        """Tolerances (queue, walking) must be 0–1."""
        if v.value < 0 or v.value > 1:
            raise ValueError("Tolerance value must be in range 0–1")
        return v


# ============================================================================
# ACCESSIBILITY & SAFETY
# ============================================================================


class AccessibilityRequirements(ParkMindBaseModel):
    """
    Hard constraints from guest accessibility declarations.

    Never traded against utility. Hard rules in ConstraintChecker.
    Separate aggregate, keyed by guest_id, loaded per-run from session store.
    Never checkpointed in LangGraph state. [C19]

    Privacy handling:
    - Store derived flag, not underlying statement
    - Require explicit consent before persisting
    - Default to session_only retention
    - Never send raw data to LLM unless necessary to phrase explanation

    §12, §33 [C15, C19]
    """

    guest_id: str
    daily_walking_limit_minutes: int | None = Field(default=None, ge=0)
    rest_frequency_minutes: int | None = Field(
        default=None, ge=0
    )  # Minutes between rest breaks
    mobility_requirements: list[MobilityRequirement] = Field(default_factory=list)
    heat_sensitivity: bool = False
    ride_restrictions: list[RideRestriction] = Field(default_factory=list)
    consent: bool  # Explicit consent required before persisting
    retention_policy: Literal["session_only", "persisted"] = "session_only"

    @model_validator(mode="after")
    def check_consent_required(self) -> "AccessibilityRequirements":
        """Accessibility data requires explicit consent."""
        if not self.consent and (
            self.daily_walking_limit_minutes is not None
            or self.mobility_requirements
            or self.heat_sensitivity
            or self.ride_restrictions
        ):
            raise ValueError(
                "explicit consent=True required before storing accessibility requirements"
            )
        return self


# ============================================================================
# PLANNING & EXECUTION
# ============================================================================


class PartyConstraints(ParkMindBaseModel):
    """
    Group-level constraints and must-do attractions.

    No longer embeds profiles or accessibility (separate aggregates keyed by guest_id).
    Removed free-form preferences dict from v1.

    §33 [C15]
    """

    party_size: int = Field(gt=0)
    guests: list[Guest]
    must_do: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    lunch_window: TimeWindow | None = None
    departure_time: datetime
    party_walking_budget_minutes: int | None = Field(
        default=None, ge=0
    )  # Optional global constraint (rule 6)
    constraints_version: int


# ============================================================================
# PARK INFRASTRUCTURE
# ============================================================================


class Attraction(ParkMindBaseModel):
    """
    Single attraction metadata from park data source.

    Immutable park topology — loaded from ThemeparksWiki.
    Used for routing, constraint checking, and preference scoring.

    §33
    """

    node_id: str
    name: str
    category: AttractionCategory
    height_restriction_cm: int | None = Field(default=None, ge=0)
    typical_wait_minutes: int = Field(ge=0)
    outdoor: bool = False


class Park(ParkMindBaseModel):
    """
    Park metadata and operating window.

    Immutable park-level facts from ThemeparksWiki.

    §33
    """

    park_id: str
    name: str
    opening_time: datetime
    closing_time: datetime
    outdoor: bool = False


class Stop(ParkMindBaseModel):
    """
    Single stop in a plan: attraction, show, meal, or rest.

    Tracks which guests are served (may be < party size for height restrictions).
    Kind determines the type of stop. [C13]

    §33 / §17
    """

    node_id: str
    kind: StopKind
    arrival_time: datetime
    departure_time: datetime
    expected_wait_minutes: float = Field(ge=0)
    walking_minutes: float = Field(ge=0)
    utility: float
    served_guests: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_stop_times(self) -> "Stop":
        if self.departure_time <= self.arrival_time:
            raise ValueError("departure_time must be after arrival_time")
        return self


class Plan(ParkMindBaseModel):
    """
    Complete itinerary for a party on a given day.

    Validated by ConstraintChecker (11 rules) before proposal.
    Only approved plans become active.
    unmet_must_do tracks must-dos unavailable for the whole horizon (graceful degradation). [C18]

    §33 [C18]
    """

    plan_id: str
    version: int
    stops: list[Stop] = Field(default_factory=list)
    total_wait_minutes: float = Field(ge=0)
    total_walking_minutes: float = Field(ge=0)
    objective_value: float
    per_guest_satisfaction: dict[str, float] = Field(
        default_factory=dict
    )  # Normalized by eligible set [C20]
    unmet_must_do: list[str] = Field(
        default_factory=list
    )  # Must-dos unavailable for the whole horizon; still a valid plan
    provenance: "Provenance"  # Metadata for reproducibility


class PlanExecutionState(ParkMindBaseModel):
    """
    Execution progress of an active plan.

    Feeds BEHIND_SCHEDULE detection, replanner stop-preservation, and the simulator.
    Separate aggregate, fed to replanner to preserve completed stops.

    §33 / §9 [C13]
    """

    plan_id: str
    completed_stop_ids: list[str] = Field(default_factory=list)
    skipped_stop_ids: list[str] = Field(default_factory=list)
    current_location_node_id: str | None = None
    as_of: datetime
    walking_minutes_consumed: dict[str, float] = Field(
        default_factory=dict
    )  # Per guest
    remaining_walking_cap_minutes: dict[str, float] = Field(
        default_factory=dict
    )  # Per guest, remaining budget for GUEST_FATIGUE detection


# ============================================================================
# ORCHESTRATION
# ============================================================================


class FairnessConfig(ParkMindBaseModel):
    """
    Fairness tuning for group preference resolution.

    Configurable, evaluated experimentally. [C04]
    """

    lambda_fairness: float = Field(ge=0)  # Penalty weight for fairness gap
    min_satisfaction_floor: float = Field(
        ge=0, le=1
    )  # Configurable minimum satisfaction per guest


class GroupObjective(ParkMindBaseModel):
    """
    Resolved group preferences + hard constraints + fairness config.

    Output of GroupResolver (deterministic).
    Input to PreferenceScorer and Optimizer.
    Single source of truth for event thresholds (profile-aware, derived from most-sensitive guest).

    §11, §33 [C12]
    """

    objective_version: str
    weights: dict[str, float]  # Scorer coefficients (λq, λw, λr, λc, λf, affinity weights)
    per_guest_eligible: dict[str, list[str]] = Field(
        default_factory=dict
    )  # guest_id -> attraction ids allowed by rules 2, 9, 10
    hard_constraints: HardConstraintSet
    fairness: FairnessConfig
    event_thresholds: EventThresholds  # Queue spike / walking overrun; most-sensitive guest
    weight_provenance: dict[str, PreferenceSource] = Field(
        default_factory=dict
    )  # Per weight: stated, learned, or default


class LiveContext(ParkMindBaseModel):
    """
    Snapshot of live data gathered by agentic or deterministic context loader.

    Drives reproducibility: a plan is a deterministic function of LiveContext.
    coverage_report determines whether fallback loader is invoked.
    tool_trace records which tools the LLM called and why.

    §8.1, §33 [C11]
    """

    snapshot_id: str
    retrieved_at: datetime
    waits: dict[str, "WaitEstimate"] = Field(default_factory=dict)
    statuses: dict[str, AttractionStatus] = Field(default_factory=dict)
    showtimes: dict[str, list[datetime]] = Field(
        default_factory=dict
    )  # attraction_id -> [times]
    weather: list["WeatherHour"] = Field(default_factory=list)
    accessibility_results: list["AccessibilityCheck"] = Field(default_factory=list)
    coverage: CoverageReport
    tool_trace: list[ToolCall] = Field(default_factory=list)


class Event(ParkMindBaseModel):
    """
    Typed event from monitor or user report.

    Severity and requires_replan are SET BY EVENTPOLICY ONLY, never by construction. [Invariant #4]
    Idempotency key (event_id) covers both monitor and user sources.

    §24, §33 [C10]
    """

    event_id: str  # Idempotency key across sources
    type: EventType
    source: EventSource
    attraction_id: str | None = None
    guest_id: str | None = None
    location_node_id: str | None = None
    severity: EventSeverity | None = None  # Set by EventPolicy
    confidence: float = Field(ge=0, le=1)
    timestamp: datetime
    requires_replan: bool | None = None  # Set by EventPolicy
    delta_minutes: int | None = None  # Minutes of impact (e.g., wait time increase)
    minutes_behind: int | None = None  # How far behind schedule
    affected_hours: float | None = None  # Estimated hours of visitor impact

    @model_validator(mode="after")
    def validate_event_fields(self) -> "Event":
        """Validate event-type-specific fields."""
        if self.type in (EventType.ATTRACTION_DOWN, EventType.WAIT_SPIKE) and not self.attraction_id:
            raise ValueError(f"{self.type} requires attraction_id")
        if self.type == EventType.GUEST_FATIGUE and not self.guest_id:
            raise ValueError(f"{self.type} requires guest_id")
        return self


class Proposal(ParkMindBaseModel):
    """
    Candidate plan presented to the user for approval.

    Holds the base plan, candidate plan, and diff for human decision-making.
    Approval status tracks whether it was approved, rejected, edited, or superseded by a newer event. [C18]

    §22, §33 [C18]
    """

    proposal_id: str
    base_plan_id: str
    candidate_plan_id: str
    reason: str  # Why this plan is proposed (e.g., "TRON is down")
    triggering_event_id: str | None = None
    diff: PlanDiff
    explanation: str
    approval_status: ApprovalStatus
    rejection_reason: RejectionReason | None = None
    provenance: "Provenance"


class Provenance(ParkMindBaseModel):
    """
    Complete metadata for reproducibility and explainability.

    Answers "where did this number come from?" without the LLM's memory.
    Weight provenance tracks stated vs learned vs default for preference-derived figures.

    §32, §40, §42, §33 [C12]
    """

    snapshot_id: str
    retrieved_at: datetime
    data_sources: list[DataSource] = Field(default_factory=list)
    forecast_strategy: str
    optimizer_strategy: str
    constraints_version: int
    objective_version: str
    preference_model_version: str
    prompt_versions: dict[str, str] = Field(
        default_factory=dict
    )  # elicit / explain / interpret
    knowledge_corpus_version: str | None = None
    weight_provenance: dict[str, PreferenceSource] = Field(
        default_factory=dict
    )  # Per weight: stated, learned, or default


# ============================================================================
# BEHAVIOR & LEARNING
# ============================================================================


class BehaviorEntry(ParkMindBaseModel):
    """
    Single behavior event from a guest.

    Idempotency key (entry_id) prevents duplicate logging.
    Used to update preferences and measure learning.

    §23, §33 [C12]
    """

    entry_id: str  # Idempotency key
    event_type: BehaviorEventType
    proposal_id: str | None = None
    plan_id: str | None = None
    decision: Literal["ACCEPTED", "REJECTED", "EDITED"] | None = None
    rejection_reason: RejectionReason | None = None
    attraction_ids: list[str] = Field(default_factory=list)
    timestamp: datetime


class BehaviorLog(ParkMindBaseModel):
    """
    Append-only event stream of guest decisions and actions.

    Separate aggregate keyed by guest_id (not embedded in profile).
    Feeds preference learning and drift measurement.

    §10, §33 [C12]
    """

    guest_id: str
    entries: list[BehaviorEntry] = Field(default_factory=list)


# Update forward references now that all models are defined
Plan.model_rebuild()
Event.model_rebuild()
Proposal.model_rebuild()
