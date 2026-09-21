"""MVP schema (P0-12).

Revision ID: 0001
Revises:

One row = typed key columns (ids, status, timestamps, FKs) plus, for aggregates,
``payload JSONB`` holding ``model_dump(mode="json")`` of the Architecture v2.2
section 33 contract. Mutable fields (proposal status) live in columns, which are
the source of truth; the payload is the immutable body.

Not created here, on purpose:
  * LangGraph checkpoint tables -- owned by langgraph-checkpoint-postgres
    (``PostgresSaver.setup()``). ``AccessibilityRequirements`` never enter state
    or checkpoints (section 34 / C19).
  * pgvector / knowledge tables -- P0-26.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


_UPGRADE = [
    # -- guests / profiles / behavior log (section 33 [C12, C15]) -------------
    """
    CREATE TABLE guests (
        guest_id   TEXT PRIMARY KEY,
        role       TEXT NOT NULL,
        height_cm  DOUBLE PRECISION,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Append-only history: the latest row per guest is the current profile.
    """
    CREATE TABLE guest_profiles (
        guest_id        TEXT NOT NULL REFERENCES guests (guest_id) ON DELETE CASCADE,
        profile_version INTEGER NOT NULL,
        payload         JSONB NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (guest_id, profile_version)
    )
    """,
    # BehaviorLog is a separate aggregate; entry_id is the idempotency key.
    """
    CREATE TABLE behavior_entries (
        guest_id    TEXT NOT NULL REFERENCES guests (guest_id) ON DELETE CASCADE,
        entry_id    TEXT NOT NULL,
        event_type  TEXT NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL,
        payload     JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (guest_id, entry_id)
    )
    """,
    """
    CREATE INDEX behavior_entries_guest_time_idx
        ON behavior_entries (guest_id, occurred_at, entry_id)
    """,
    # -- plans / proposals / active plan (section 22-23, 33 [C18]) ------------
    # Plan bodies are immutable and never "active" by a flag: a candidate is a
    # plan referenced by a proposal, the active plan is the row in active_plans.
    """
    CREATE TABLE plans (
        plan_id    TEXT PRIMARY KEY,
        version    INTEGER NOT NULL,
        thread_id  TEXT NOT NULL,
        payload    JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX plans_thread_idx ON plans (thread_id)",
    # base_plan_id carries no FK: the contract makes it mandatory even for the
    # first proposal of a thread, where no base plan exists yet (unresolved).
    """
    CREATE TABLE proposals (
        proposal_id         TEXT PRIMARY KEY,
        thread_id           TEXT NOT NULL,
        base_plan_id        TEXT NOT NULL,
        candidate_plan_id   TEXT NOT NULL REFERENCES plans (plan_id),
        triggering_event_id TEXT,
        approval_status     TEXT NOT NULL CHECK (
            approval_status IN
                ('PENDING', 'APPROVED', 'REJECTED', 'EDITED', 'SUPERSEDED')
        ),
        rejection_reason    TEXT,
        payload             JSONB NOT NULL,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        resolved_at         TIMESTAMPTZ
    )
    """,
    "CREATE INDEX proposals_candidate_idx ON proposals (candidate_plan_id)",
    # Section 43 [C18]: a concurrent event supersedes the pending proposal, so a
    # thread never holds two PENDING proposals.
    """
    CREATE UNIQUE INDEX proposals_one_pending_per_thread
        ON proposals (thread_id) WHERE approval_status = 'PENDING'
    """,
    """
    CREATE TABLE active_plans (
        thread_id    TEXT PRIMARY KEY,
        plan_id      TEXT NOT NULL REFERENCES plans (plan_id),
        proposal_id  TEXT NOT NULL REFERENCES proposals (proposal_id),
        activated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # -- execution state / events (section 33 [C13, C10]) ---------------------
    """
    CREATE TABLE execution_states (
        plan_id    TEXT PRIMARY KEY REFERENCES plans (plan_id),
        as_of      TIMESTAMPTZ NOT NULL,
        payload    JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # event_id is the idempotency key across sources (section 43 "Duplicate
    # event"); park-wide events are recorded once per thread, hence the pair.
    """
    CREATE TABLE events (
        thread_id   TEXT NOT NULL,
        event_id    TEXT NOT NULL,
        type        TEXT NOT NULL,
        source      TEXT NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL,
        payload     JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (thread_id, event_id)
    )
    """,
    "CREATE INDEX events_thread_time_idx ON events (thread_id, occurred_at, event_id)",
    # -- snapshots, section 41 [C21]: raw provider payload beside normalized ----
    """
    CREATE TABLE snapshots (
        snapshot_id  TEXT PRIMARY KEY,
        retrieved_at TIMESTAMPTZ NOT NULL,
        data_sources TEXT[] NOT NULL,
        live_context JSONB NOT NULL,
        raw_payload  JSONB NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX snapshots_retrieved_at_idx ON snapshots (retrieved_at DESC)",
    # -- attraction data ------------------------------------------------------
    """
    CREATE TABLE attractions (
        node_id    TEXT PRIMARY KEY,
        park_id    TEXT NOT NULL,
        payload    JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX attractions_park_idx ON attractions (park_id)",
    """
    CREATE TABLE park_schedules (
        park_id      TEXT NOT NULL,
        service_date DATE NOT NULL,
        payload      JSONB NOT NULL,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (park_id, service_date)
    )
    """,
    # -- id mapping (P0-10 owns the model; this is storage only) ---------------
    """
    CREATE TABLE id_mapping (
        provider      TEXT NOT NULL,
        provider_id   TEXT NOT NULL,
        entity_kind   TEXT NOT NULL,
        internal_id   TEXT NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL,
        last_seen_at  TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (provider, provider_id, entity_kind)
    )
    """,
    "CREATE INDEX id_mapping_internal_idx ON id_mapping (internal_id)",
    # -- provenance: queryable index over the Provenance embedded in plans -----
    # No FK on snapshot_id: evaluation scenarios pin a LiveContext that was
    # never collected into `snapshots`.
    """
    CREATE TABLE provenance (
        subject_kind TEXT NOT NULL CHECK (subject_kind IN ('PLAN', 'PROPOSAL')),
        subject_id   TEXT NOT NULL,
        snapshot_id  TEXT NOT NULL,
        payload      JSONB NOT NULL,
        recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (subject_kind, subject_id)
    )
    """,
    "CREATE INDEX provenance_snapshot_idx ON provenance (snapshot_id)",
    # -- accessibility (section 12, 33 [C15, C19]) ------------------------------
    # Only `persisted` + consented records may exist in any table. A
    # `session_only` record lives in the session store's memory and is
    # structurally unable to reach this table.
    """
    CREATE TABLE accessibility_requirements (
        guest_id         TEXT PRIMARY KEY REFERENCES guests (guest_id) ON DELETE CASCADE,
        retention_policy TEXT NOT NULL CHECK (retention_policy = 'persisted'),
        consent          BOOLEAN NOT NULL CHECK (consent),
        payload          JSONB NOT NULL CHECK (
            payload ->> 'retention_policy' = 'persisted'
            AND payload ->> 'consent' = 'true'
        ),
        stored_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]

# Reverse dependency order.
_DOWNGRADE_TABLES = [
    "accessibility_requirements",
    "provenance",
    "id_mapping",
    "park_schedules",
    "attractions",
    "snapshots",
    "events",
    "execution_states",
    "active_plans",
    "proposals",
    "plans",
    "behavior_entries",
    "guest_profiles",
    "guests",
]


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for table in _DOWNGRADE_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
