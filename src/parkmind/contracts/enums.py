"""
Closed taxonomies for ParkMind domain using Python enums.

All taxonomies are enums to ensure type safety, autocomplete, and fail-closed matching
in constraint validation. Free strings would cause check_accessibility to fail
on every attraction when a guest has restrictions.

Pydantic v2 serializes these to/from JSON automatically.

§33 Domain Contracts — v2.2 [C15]
"""

from enum import Enum


# Attraction & Park state
class AttractionStatus(str, Enum):
    OPERATING = "OPERATING"
    DOWN = "DOWN"
    CLOSED = "CLOSED"
    REFURBISHMENT = "REFURBISHMENT"


# Schedule types
class StopKind(str, Enum):
    ATTRACTION = "ATTRACTION"
    SHOW = "SHOW"
    MEAL = "MEAL"
    REST = "REST"


# Guest preferences — sensitivities
class SensitivityKind(str, Enum):
    INTENSITY = "INTENSITY"
    DARKNESS = "DARKNESS"
    HEIGHTS = "HEIGHTS"
    WATER = "WATER"
    LOUD_NOISE = "LOUD_NOISE"
    SPINNING = "SPINNING"


class SensitivityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# Attraction category taxonomy
class AttractionCategory(str, Enum):
    THRILL = "THRILL"
    FAMILY = "FAMILY"
    DARK_RIDE = "DARK_RIDE"
    SHOW = "SHOW"
    WATER = "WATER"
    CHARACTER = "CHARACTER"
    TRANSPORT = "TRANSPORT"


# Accessibility requirements
class MobilityRequirement(str, Enum):
    LIMITED_WALKING = "LIMITED_WALKING"
    WHEELCHAIR = "WHEELCHAIR"
    ECV = "ECV"
    STROLLER_AS_WHEELCHAIR = "STROLLER_AS_WHEELCHAIR"


# Safety-notice derived restrictions (one-to-one with published park taxonomy).
# Never store the underlying statement; only the derived flag. [C15]
class RideRestriction(str, Enum):
    NOT_RECOMMENDED_HIGH_G_FORCE = "NOT_RECOMMENDED_HIGH_G_FORCE"
    NOT_RECOMMENDED_MOTION_SENSITIVITY = "NOT_RECOMMENDED_MOTION_SENSITIVITY"
    NOT_RECOMMENDED_HEART_CONDITION = "NOT_RECOMMENDED_HEART_CONDITION"
    NOT_RECOMMENDED_BACK_NECK = "NOT_RECOMMENDED_BACK_NECK"
    NOT_RECOMMENDED_EXPECTANT = "NOT_RECOMMENDED_EXPECTANT"
    REQUIRES_TRANSFER_FROM_WHEELCHAIR = "REQUIRES_TRANSFER_FROM_WHEELCHAIR"
    NO_SERVICE_ANIMALS = "NO_SERVICE_ANIMALS"


# Event sourcing
class EventSource(str, Enum):
    MONITOR = "monitor"
    USER = "user"


# Behavior tracking
class BehaviorEventType(str, Enum):
    PROPOSAL_ACCEPTED = "PROPOSAL_ACCEPTED"
    PROPOSAL_REJECTED = "PROPOSAL_REJECTED"
    PROPOSAL_EDITED = "PROPOSAL_EDITED"
    ATTRACTION_COMPLETED = "ATTRACTION_COMPLETED"
    ATTRACTION_SKIPPED = "ATTRACTION_SKIPPED"
    ALTERNATIVE_REQUESTED = "ALTERNATIVE_REQUESTED"


# Rejection reasons — categorized, not just counted.
# Used to diagnose why recommendations are rejected. [§23]
class RejectionReason(str, Enum):
    TOO_MUCH_WALKING = "TOO_MUCH_WALKING"
    TOO_MUCH_WAITING = "TOO_MUCH_WAITING"
    WRONG_ATTRACTION_TYPE = "WRONG_ATTRACTION_TYPE"
    BAD_TIMING = "BAD_TIMING"
    GUEST_LEFT_OUT = "GUEST_LEFT_OUT"
    OTHER = "OTHER"


# Event severity — set by EventPolicy only, never by construction. [Invariant #4]
class EventSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# Proposal approval state — manages the unapproved-plan invariant. [§23]
class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EDITED = "EDITED"
    SUPERSEDED = "SUPERSEDED"


# Preference source — tracks stated vs learned vs default. [C12]
class PreferenceSource(str, Enum):
    STATED = "stated"
    LEARNED = "learned"
    DEFAULT = "default"


# Guest role — identity, not preference
class GuestRole(str, Enum):
    ADULT = "adult"
    CHILD = "child"


# Planning pace preference
class PlanningPace(str, Enum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    MAXIMIZER = "maximizer"


# Planning style preference
class PlanningStyle(str, Enum):
    STRUCTURED = "structured"
    FLEXIBLE = "flexible"
    SPONTANEOUS = "spontaneous"


# Data sources for provenance
class DataSource(str, Enum):
    THEMEPARKS_WIKI = "themeparks_wiki"
    QUEUE_TIMES = "queue_times"
    OPEN_METEO = "open_meteo"
    CACHE = "cache"
    HISTORICAL = "historical"
