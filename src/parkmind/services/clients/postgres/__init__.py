"""PostgreSQL adapters (psycopg 3) behind the repository ports in
``parkmind.services.ports``. The schema is owned by Alembic
(``database/migrations``); nothing here creates or alters tables.

Each repository takes a caller-owned connection, so several can share one
transaction. ``seed`` and ``migrate`` are imported by module (dev tooling), not
re-exported here.
"""

from .attraction_repository import PostgresAttractionRepository
from .behavior_log_repository import PostgresBehaviorLogRepository
from .connection import connect
from .event_repository import PostgresEventRepository
from .execution_state_repository import PostgresExecutionStateRepository
from .guest_repository import PostgresGuestRepository
from .id_mapping_repository import PostgresIdMappingRepository
from .plan_repository import PostgresPlanRepository
from .profile_repository import PostgresProfileRepository
from .proposal_repository import PostgresProposalRepository
from .provenance_repository import PostgresProvenanceRepository
from .session_store import PostgresSessionStore
from .snapshot_repository import PostgresSnapshotRepository

__all__ = [
    "PostgresAttractionRepository",
    "PostgresBehaviorLogRepository",
    "PostgresEventRepository",
    "PostgresExecutionStateRepository",
    "PostgresGuestRepository",
    "PostgresIdMappingRepository",
    "PostgresPlanRepository",
    "PostgresProfileRepository",
    "PostgresProposalRepository",
    "PostgresProvenanceRepository",
    "PostgresSessionStore",
    "PostgresSnapshotRepository",
    "connect",
]
