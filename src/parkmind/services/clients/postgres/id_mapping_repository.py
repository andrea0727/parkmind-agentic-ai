"""PostgreSQL adapter for ``IdMappingRepository`` (storage only; the model lives in the core)."""

from collections.abc import Iterable
from datetime import datetime

from parkmind.services.clients.postgres.connection import PostgresRepositoryBase
from parkmind.services.ports.errors import IdMappingConflictError


class PostgresIdMappingRepository(PostgresRepositoryBase):
    def record(
        self,
        provider: str,
        provider_id: str,
        entity_kind: str,
        internal_id: str,
        *,
        seen_at: datetime,
    ) -> None:
        # One statement, race-free: the DO UPDATE only fires when the stored
        # internal_id matches, so a conflicting re-map returns no row and is
        # raised instead of being overwritten.
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO id_mapping
                    (provider, provider_id, entity_kind, internal_id,
                     first_seen_at, last_seen_at)
                VALUES (%(provider)s, %(provider_id)s, %(entity_kind)s,
                        %(internal_id)s, %(seen_at)s, %(seen_at)s)
                ON CONFLICT (provider, provider_id, entity_kind) DO UPDATE
                    SET first_seen_at = LEAST(id_mapping.first_seen_at, EXCLUDED.first_seen_at),
                        last_seen_at = GREATEST(id_mapping.last_seen_at, EXCLUDED.last_seen_at)
                    WHERE id_mapping.internal_id = EXCLUDED.internal_id
                RETURNING internal_id
                """,
                {
                    "provider": provider,
                    "provider_id": provider_id,
                    "entity_kind": entity_kind,
                    "internal_id": internal_id,
                    "seen_at": seen_at,
                },
            )
            if cur.fetchone() is None:
                raise IdMappingConflictError(
                    "provider id is already mapped to a different internal id"
                )

    def resolve(self, provider: str, provider_id: str, entity_kind: str) -> str | None:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT internal_id FROM id_mapping
                WHERE provider = %s AND provider_id = %s AND entity_kind = %s
                """,
                (provider, provider_id, entity_kind),
            )
            row = cur.fetchone()
        return None if row is None else str(row["internal_id"])

    def resolve_many(
        self, provider: str, keys: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], str]:
        wanted = [(str(provider_id), str(kind)) for provider_id, kind in keys]
        if not wanted:
            return {}
        with self._tx() as cur:
            cur.execute(
                """
                SELECT m.provider_id, m.entity_kind, m.internal_id
                FROM id_mapping m
                JOIN unnest(%s::text[], %s::text[]) AS k(provider_id, entity_kind)
                  ON m.provider_id = k.provider_id AND m.entity_kind = k.entity_kind
                WHERE m.provider = %s
                """,
                ([p for p, _ in wanted], [k for _, k in wanted], provider),
            )
            rows = cur.fetchall()
        return {
            (r["provider_id"], r["entity_kind"]): str(r["internal_id"]) for r in rows
        }

    def provider_ids_for(self, internal_id: str) -> list[tuple[str, str, str]]:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT provider, provider_id, entity_kind FROM id_mapping
                WHERE internal_id = %s ORDER BY provider, entity_kind, provider_id
                """,
                (internal_id,),
            )
            rows = cur.fetchall()
        return [(r["provider"], r["provider_id"], r["entity_kind"]) for r in rows]
