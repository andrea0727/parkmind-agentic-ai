"""services/ports — Protocol definitions only.

Convention (P0-07 establishes this; P0-08 adds WeatherPort, P0-09 adds
RoutingPort): one Protocol per module, named after the port (park_data.py,
weather.py, routing.py, ...), re-exported here so callers do
`from parkmind.services.ports import ParkDataPort`. No implementation code
lives in this package — it is the seam .importlinter's core-forbidden-imports
contract protects.

P0-12 adds the repository ports and SessionStore. ``errors.py`` is the one
non-Protocol module: exceptions only, so callers can catch adapter failures
without importing an adapter (and therefore a DB driver).
"""

from .attraction_repository import AttractionRepository
from .behavior_log_repository import BehaviorLogRepository
from .errors import (
    ConflictError,
    ConsentRequiredError,
    IdMappingConflictError,
    InvalidStateTransitionError,
    NotApprovedError,
    NotFoundError,
    PendingProposalExistsError,
    PlanImmutableError,
    ProfileVersionConflictError,
    ProvenanceConflictError,
    RepositoryError,
    RepositoryUnavailableError,
    StoredDataError,
)
from .event_repository import EventRepository
from .execution_state_repository import ExecutionStateRepository
from .guest_repository import GuestRepository
from .id_mapping_repository import IdMappingRepository
from .park_data import ParkDataPort
from .plan_repository import PlanRepository
from .profile_repository import ProfileRepository
from .proposal_repository import ProposalRepository
from .provenance_repository import ProvenanceRepository, ProvenanceSubjectKind
from .session_store import SessionStore
from .snapshot_repository import RawPayload, SnapshotRepository

__all__ = [
    "AttractionRepository",
    "BehaviorLogRepository",
    "ConflictError",
    "ConsentRequiredError",
    "EventRepository",
    "ExecutionStateRepository",
    "GuestRepository",
    "IdMappingConflictError",
    "IdMappingRepository",
    "InvalidStateTransitionError",
    "NotApprovedError",
    "NotFoundError",
    "ParkDataPort",
    "PendingProposalExistsError",
    "PlanImmutableError",
    "PlanRepository",
    "ProfileRepository",
    "ProfileVersionConflictError",
    "ProposalRepository",
    "ProvenanceConflictError",
    "ProvenanceRepository",
    "ProvenanceSubjectKind",
    "RawPayload",
    "RepositoryError",
    "RepositoryUnavailableError",
    "SessionStore",
    "SnapshotRepository",
    "StoredDataError",
]
