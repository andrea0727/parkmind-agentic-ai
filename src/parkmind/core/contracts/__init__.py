"""
ParkMind domain contracts.

Complete set of Pydantic v2 models for guest modeling, planning, and orchestration.
Frozen before agent development. v2.2 baseline.

§33 Domain Contracts
"""

# Enums
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
    RuleId,
)

# Base types
from .base import (
    ParkMindBaseModel,
    TimeWindow,
    WaitEstimate,
    WeatherHour,
    AccessibilityCheck,
    ToolCall,
    CoverageReport,
    CheckResult,
    PlanDiff,
    HardConstraintSet,
    EventThresholds,
    PARK_TZ,
)

# Domain models
from .models import (
    Guest,
    PreferenceValue,
    GuestProfile,
    AccessibilityRequirements,
    PartyConstraints,
    Attraction,
    Park,
    Stop,
    Plan,
    PlanExecutionState,
    FairnessConfig,
    GroupObjective,
    LiveContext,
    Event,
    Proposal,
    Provenance,
    BehaviorEntry,
    BehaviorLog,
)

__all__ = [
    # Enums
    "AttractionStatus",
    "StopKind",
    "SensitivityKind",
    "SensitivityLevel",
    "AttractionCategory",
    "MobilityRequirement",
    "RideRestriction",
    "EventSource",
    "BehaviorEventType",
    "RejectionReason",
    "EventSeverity",
    "EventType",
    "ApprovalStatus",
    "PreferenceSource",
    "GuestRole",
    "PlanningPace",
    "PlanningStyle",
    "DataSource",
    "RuleId",
    # Base
    "ParkMindBaseModel",
    "TimeWindow",
    "WaitEstimate",
    "WeatherHour",
    "AccessibilityCheck",
    "ToolCall",
    "CoverageReport",
    "CheckResult",
    "PlanDiff",
    "HardConstraintSet",
    "EventThresholds",
    "PARK_TZ",
    # Models
    "Guest",
    "PreferenceValue",
    "GuestProfile",
    "AccessibilityRequirements",
    "PartyConstraints",
    "Attraction",
    "Park",
    "Stop",
    "Plan",
    "PlanExecutionState",
    "FairnessConfig",
    "GroupObjective",
    "LiveContext",
    "Event",
    "Proposal",
    "Provenance",
    "BehaviorEntry",
    "BehaviorLog",
]
