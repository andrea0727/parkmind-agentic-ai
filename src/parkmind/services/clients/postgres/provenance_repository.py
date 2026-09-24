"""PostgreSQL adapter for ``ProvenanceRepository``.

The plan and proposal adapters build one of these on their own connection and
call ``record`` inside their transaction, so a plan and its provenance row are
stored atomically.
"""

from typing import cast, get_args

from parkmind.core.contracts import Provenance
from parkmind.services.clients.postgres.codec import from_payload, to_jsonb
from parkmind.services.clients.postgres.connection import PostgresRepositoryBase
from parkmind.services.ports.errors import ProvenanceConflictError, StoredDataError
from parkmind.services.ports.provenance_repository import ProvenanceSubjectKind


class PostgresProvenanceRepository(PostgresRepositoryBase):
    def record(
        self,
        subject_kind: ProvenanceSubjectKind,
        subject_id: str,
        provenance: Provenance,
    ) -> None:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO provenance (subject_kind, subject_id, snapshot_id, payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (subject_kind, subject_id) DO NOTHING
                RETURNING subject_id
                """,
                (subject_kind, subject_id, provenance.snapshot_id, to_jsonb(provenance)),
            )
            if cur.fetchone() is not None:
                return

        # Already recorded: fine if identical (retry), otherwise provenance
        # would silently stop describing what produced the subject.
        if self.get(subject_kind, subject_id) != provenance:
            raise ProvenanceConflictError("different provenance is already recorded")

    def get(self, subject_kind: ProvenanceSubjectKind, subject_id: str) -> Provenance | None:
        with self._tx() as cur:
            cur.execute(
                "SELECT payload FROM provenance WHERE subject_kind = %s AND subject_id = %s",
                (subject_kind, subject_id),
            )
            row = cur.fetchone()
        return None if row is None else from_payload(Provenance, row["payload"], what="provenance")

    def subjects_for_snapshot(
        self, snapshot_id: str
    ) -> list[tuple[ProvenanceSubjectKind, str]]:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT subject_kind, subject_id FROM provenance
                WHERE snapshot_id = %s ORDER BY subject_kind, subject_id
                """,
                (snapshot_id,),
            )
            rows = cur.fetchall()

        allowed = get_args(ProvenanceSubjectKind)
        subjects: list[tuple[ProvenanceSubjectKind, str]] = []
        for row in rows:
            if row["subject_kind"] not in allowed:
                raise StoredDataError("stored provenance has an unknown subject kind")
            subjects.append((cast(ProvenanceSubjectKind, row["subject_kind"]), row["subject_id"]))
        return subjects
