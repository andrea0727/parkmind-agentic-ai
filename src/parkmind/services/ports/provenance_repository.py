"""ProvenanceRepository -- "where did this number come from?" (section 33, 40).

``Plan`` and ``Proposal`` embed their ``Provenance``; this port indexes it so
questions like "which plans were built from snapshot X" need no JSON scan. The
plan and proposal adapters record provenance in the same transaction that
stores the plan or proposal.
"""

from typing import Literal, Protocol

from parkmind.core.contracts import Provenance

ProvenanceSubjectKind = Literal["PLAN", "PROPOSAL"]


class ProvenanceRepository(Protocol):
    def record(
        self,
        subject_kind: ProvenanceSubjectKind,
        subject_id: str,
        provenance: Provenance,
    ) -> None:
        """Record provenance for a plan or proposal.

        Provenance is immutable: an identical re-record is a no-op, a different
        one raises ``ProvenanceConflictError``.
        """
        ...

    def get(
        self, subject_kind: ProvenanceSubjectKind, subject_id: str
    ) -> Provenance | None: ...

    def subjects_for_snapshot(
        self, snapshot_id: str
    ) -> list[tuple[ProvenanceSubjectKind, str]]:
        """Plans/proposals whose provenance names ``snapshot_id``."""
        ...
