"""Snapshot normalizer version (P0-11).

Revision ID: 0002
Revises: 0001

Each snapshot records which normalizer version produced its ``live_context``,
so the re-normalization command (Architecture 41, C21) can find rows built by
an older normalizer and rebuild them from ``raw_payload``. Existing rows were
all written by version 1.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE snapshots
            ADD COLUMN normalizer_version INTEGER NOT NULL DEFAULT 1
                CHECK (normalizer_version >= 1)
        """
    )
    op.execute("CREATE INDEX snapshots_normalizer_version_idx ON snapshots (normalizer_version)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS snapshots_normalizer_version_idx")
    op.execute("ALTER TABLE snapshots DROP COLUMN IF EXISTS normalizer_version")
