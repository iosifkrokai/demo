"""The server-side thread: threads and turns."""

from __future__ import annotations

from alembic import op

revision = "0004_server_thread"
down_revision = "0003_drop_anonymous_client"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS threads (
            id         TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS turns (
            thread_id   TEXT NOT NULL REFERENCES threads (id) ON DELETE CASCADE,
            revision    INT NOT NULL,
            query       TEXT NOT NULL,
            submission  JSONB NOT NULL DEFAULT '{}'::jsonb,
            measurement JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (thread_id, revision)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS turns_thread_revision_ix ON turns (thread_id, revision DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS turns_thread_revision_ix")
    op.execute("DROP TABLE IF EXISTS turns")
    op.execute("DROP TABLE IF EXISTS threads")
