"""
Closed taxonomies for ParkMind domain.

All taxonomies are Literal enums to ensure type safety and fail-closed matching
in constraint validation. Free strings would cause check_accessibility to fail
on every attraction when a guest has restrictions.

§33 Domain Contracts — v2.2 [C15]
"""

from typing import Literal

# Attraction & Park state
AttractionStatus = Literal["OPERATING", "DOWN", "CLOSED", "REFURBISHMENT"]

# Schedule types
StopKind = Literal["ATTRACTION", "SHOW", "MEAL", "REST"]

# Guest preferences — sensitivities
SensitivityKind = Literal[
    "INTENSITY",
    "DARKNESS",
    "HEIGHTS",
    "WATER",
    "LOUD_NOISE",
    "SPINNING",
]
SensitivityLevel = Literal["LOW", "MEDIUM", "HIGH"]

# Attraction category taxonomy
AttractionCategory = Literal[
    "THRILL",
    "FAMILY",
    "DARK_RIDE",
    "SHOW",
    "WATER",
    "CHARACTER",
    "TRANSPORT",
]

# Accessibility requirements
MobilityRequirement = Literal[
    "LIMITED_WALKING",
    "WHEELCHAIR",
    "ECV",
    "STROLLER_AS_WHEELCHAIR",
]

# Safety-notice derived restrictions (one-to-one with published park taxonomy).
# Never store the underlying statement; only the derived flag. [C15]
RideRestriction = Literal[
    "NOT_RECOMMENDED_HIGH_G_FORCE",
    "NOT_RECOMMENDED_MOTION_SENSITIVITY",
    "NOT_RECOMMENDED_HEART_CONDITION",
    "NOT_RECOMMENDED_BACK_NECK",
    "NOT_RECOMMENDED_EXPECTANT",
    "REQUIRES_TRANSFER_FROM_WHEELCHAIR",
    "NO_SERVICE_ANIMALS",
]

# Event sourcing
EventSource = Literal["monitor", "user"]

# Behavior tracking
BehaviorEventType = Literal[
    "PROPOSAL_ACCEPTED",
    "PROPOSAL_REJECTED",
    "PROPOSAL_EDITED",
    "ATTRACTION_COMPLETED",
    "ATTRACTION_SKIPPED",
    "ALTERNATIVE_REQUESTED",
]

# Rejection reasons — categorized, not just counted.
# Used to diagnose why recommendations are rejected. [§23]
RejectionReason = Literal[
    "TOO_MUCH_WALKING",
    "TOO_MUCH_WAITING",
    "WRONG_ATTRACTION_TYPE",
    "BAD_TIMING",
    "GUEST_LEFT_OUT",
    "OTHER",
]

# Event severity — set by EventPolicy only, never by construction. [Invariant #4]
EventSeverity = Literal["LOW", "MEDIUM", "HIGH"]

# Proposal approval state — manages the unapproved-plan invariant. [§23]
ApprovalStatus = Literal["PENDING", "APPROVED", "REJECTED", "EDITED", "SUPERSEDED"]

# Preference source — tracks stated vs learned vs default. [C12]
PreferenceSource = Literal["stated", "learned", "default"]

# Guest role — identity, not preference
GuestRole = Literal["adult", "child"]

# Planning pace preference
PlanningPace = Literal["relaxed", "balanced", "maximizer"]

# Planning style preference
PlanningStyle = Literal["structured", "flexible", "spontaneous"]

# Data sources for provenance
DataSource = Literal[
    "themeparks_wiki",
    "queue_times",
    "open_meteo",
    "cache",
    "historical",
]
