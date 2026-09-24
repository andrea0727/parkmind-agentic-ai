"""Exceptions raised by repository and session-store adapters.

They live beside the ports so callers (use_cases, graph) can catch them without
importing an adapter -- and therefore without importing a DB driver. Distinct
types for unavailable / not-found / conflict / stored-data drift, mirroring
.claude/rules/clients.md: nothing is silently coerced into a best guess.
"""


class RepositoryError(Exception):
    """Base class for every repository / session-store failure."""


class RepositoryUnavailableError(RepositoryError):
    """The storage backend could not be reached or failed transiently."""


class NotFoundError(RepositoryError):
    """A write referenced an entity that does not exist (e.g. unknown guest)."""


class StoredDataError(RepositoryError):
    """A stored row no longer validates against its contract (schema drift).

    The message never includes the stored values: payloads can hold sensitive
    derived flags (section 12), and error text ends up in logs.
    """


class ConflictError(RepositoryError):
    """A write conflicts with data that is already stored."""


class ProfileVersionConflictError(ConflictError):
    """``profile_version`` is not greater than the latest stored version."""


class PlanImmutableError(ConflictError):
    """A stored plan body is immutable; re-saving a different body is refused."""


class ProposalImmutableError(ConflictError):
    """A stored proposal's body is immutable; only its resolution may change."""


class PendingProposalExistsError(ConflictError):
    """The thread already holds a PENDING proposal (section 43 [C18])."""


class IdMappingConflictError(ConflictError):
    """A provider id is already mapped to a different internal id.

    Surfaced instead of overwritten so duplicate mappings are never silently
    merged (backlog P0-10).
    """


class ProvenanceConflictError(ConflictError):
    """Provenance already recorded for the subject differs from the new one."""


class InvalidStateTransitionError(RepositoryError):
    """A proposal may only leave PENDING; terminal states are final."""


class NotApprovedError(RepositoryError):
    """A plan can only become active through an APPROVED proposal (section 23)."""


class ConsentRequiredError(RepositoryError):
    """Accessibility data may only be persisted with explicit consent (section 12)."""
