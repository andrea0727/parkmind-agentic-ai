"""services/ports — Protocol definitions only.

Convention (P0-07 establishes this; P0-08 adds WeatherPort, P0-09 adds
RoutingPort): one Protocol per module, named after the port (park_data.py,
weather.py, routing.py, ...), re-exported here so callers do
`from parkmind.services.ports import ParkDataPort`. No implementation code
lives in this package — it is the seam .importlinter's core-forbidden-imports
contract protects.

P0-12 adds the repository ports and SessionStore; P0-26a adds KnowledgeStore;
P0-18 adds ForecastStrategy.
``errors.py`` is the one
non-Protocol module: exceptions only, so callers can catch adapter failures
without importing an adapter (and therefore a DB driver).
"""

from .attraction_repository import AttractionRepository
from .behavior_log_repository import BehaviorLogRepository
from .errors import (
    ConflictError,
    ConsentRequiredError,
    ForecastSourceError,
    IdMappingConflictError,
    InvalidRouteError,
    InvalidStateTransitionError,
    NotApprovedError,
    NotFoundError,
    PendingProposalExistsError,
    PlanImmutableError,
    ProfileVersionConflictError,
    ProposalImmutableError,
    ProvenanceConflictError,
    RepositoryError,
    RepositoryUnavailableError,
    RouteNotFoundError,
    RoutingError,
    RoutingNotFoundError,
    RoutingSchemaError,
    RoutingUnavailableError,
    StoredDataError,
)
from .event_repository import EventRepository
from .execution_state_repository import ExecutionStateRepository
from .forecast import ForecastStrategy, WaitForecast
from .guest_repository import GuestRepository
from .id_mapping_repository import EntityKind, IdMappingRepository
from .knowledge_store import KnowledgeStore
from .park_data import ParkDataPort
from .plan_repository import PlanRepository
from .profile_repository import ProfileRepository
from .proposal_repository import ProposalRepository
from .provenance_repository import ProvenanceRepository, ProvenanceSubjectKind
from .routing import RoutingPort, WalkBasis, WalkEstimate, WalkEstimator
from .session_store import SessionStore
from .snapshot_repository import RawPayload, SnapshotMeta, SnapshotRepository
from .weather import WeatherPort

__all__ = [
    "AttractionRepository",
    "BehaviorLogRepository",
    "ConflictError",
    "ConsentRequiredError",
    "EntityKind",
    "EventRepository",
    "ExecutionStateRepository",
    "ForecastSourceError",
    "ForecastStrategy",
    "GuestRepository",
    "IdMappingConflictError",
    "IdMappingRepository",
    "InvalidRouteError",
    "InvalidStateTransitionError",
    "KnowledgeStore",
    "NotApprovedError",
    "NotFoundError",
    "ParkDataPort",
    "PendingProposalExistsError",
    "PlanImmutableError",
    "PlanRepository",
    "ProfileRepository",
    "ProfileVersionConflictError",
    "ProposalImmutableError",
    "ProposalRepository",
    "ProvenanceConflictError",
    "ProvenanceRepository",
    "ProvenanceSubjectKind",
    "RawPayload",
    "RepositoryError",
    "RepositoryUnavailableError",
    "RouteNotFoundError",
    "RoutingError",
    "RoutingNotFoundError",
    "RoutingPort",
    "RoutingSchemaError",
    "RoutingUnavailableError",
    "SessionStore",
    "SnapshotMeta",
    "SnapshotRepository",
    "StoredDataError",
    "WaitForecast",
    "WalkBasis",
    "WalkEstimate",
    "WalkEstimator",
    "WeatherPort",
]
