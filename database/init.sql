-- Month-1 schema: transactional guest/plan/event state only.
-- No pgvector extension this month (knowledge store is in-memory — see
-- src/parkmind/services/clients/knowledge_store.py).

CREATE TABLE IF NOT EXISTS guests (
    guest_id TEXT PRIMARY KEY,
    profile JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    base_plan_id TEXT REFERENCES plans(plan_id),
    candidate_plan_id TEXT REFERENCES plans(plan_id),
    approval_status TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS behavior_signals (
    id SERIAL PRIMARY KEY,
    guest_id TEXT NOT NULL REFERENCES guests(guest_id),
    proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
    decision TEXT NOT NULL,
    rejection_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
