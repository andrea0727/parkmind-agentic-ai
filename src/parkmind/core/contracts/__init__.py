"""
ParkMind domain contracts.

Complete set of Pydantic v2 models for guest modeling, planning, and orchestration.
Frozen before agent development. v2.2 baseline.

§33 Domain Contracts
"""

# Enums
# Base types
from .base import (
    PARK_TZ,
    AccessibilityCheck,
    CheckResult,
    ConstraintViolation,
    CoverageReport,
    EventThresholds,
    HardConstraintSet,
    ParkMindBaseModel,
    PlanDiff,
    TimeWindow,
    ToolCall,
    WaitEstimate,
    WeatherHour,
)
from .enums import (
    ApprovalStatus,
    AttractionCategory,
    AttractionStatus,
    BehaviorEventType,
    DataSource,
    EventSeverity,
    EventSource,
    EventType,
    GuestRole,
    MobilityRequirement,
    PlanningPace,
    PlanningStyle,
    PreferenceSource,
    RejectionReason,
    RideRestriction,
    RuleId,
    SensitivityKind,
    SensitivityLevel,
    StopKind,
)

# Domain models
from .models import (
    AccessibilityRequirements,
    Attraction,
    BehaviorEntry,
    BehaviorLog,
    Event,
    FairnessConfig,
    GroupObjective,
    Guest,
    GuestProfile,
    LiveContext,
    Park,
    PartyConstraints,
    Plan,
    PlanExecutionState,
    PreferenceValue,
    Proposal,
    Provenance,
    Stop,
)

__all__ = [
    "PARK_TZ",
    "AccessibilityCheck",
    "AccessibilityRequirements",
    "ApprovalStatus",
    "Attraction",
    "AttractionCategory",
    # Enums
    "AttractionStatus",
    "BehaviorEntry",
    "BehaviorEventType",
    "BehaviorLog",
    "CheckResult",
    "ConstraintViolation",
    "CoverageReport",
    "DataSource",
    "Event",
    "EventSeverity",
    "EventSource",
    "EventThresholds",
    "EventType",
    "FairnessConfig",
    "GroupObjective",
    # Models
    "Guest",
    "GuestProfile",
    "GuestRole",
    "HardConstraintSet",
    "LiveContext",
    "MobilityRequirement",
    "Park",
    # Base
    "ParkMindBaseModel",
    "PartyConstraints",
    "Plan",
    "PlanDiff",
    "PlanExecutionState",
    "PlanningPace",
    "PlanningStyle",
    "PreferenceSource",
    "PreferenceValue",
    "Proposal",
    "Provenance",
    "RejectionReason",
    "RideRestriction",
    "RuleId",
    "SensitivityKind",
    "SensitivityLevel",
    "Stop",
    "StopKind",
    "TimeWindow",
    "ToolCall",
    "WaitEstimate",
    "WeatherHour",
]
