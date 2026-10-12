"""the server-side thread: a journal of planning turns

A refinement used to be a snapshot the client re-sent on every turn, with no
server record of what had come before. These two tables give the server its own
memory of a run: `threads` is the run session, and `turns` is one row per
exchange — the query, the submission the agent proposed, and the measurement the
tools returned.

`revision` is a per-thread sequence the store mints against the rows already
present, and `(thread_id, revision)` is the key, so a turn is addressed by number
and the journal has no gaps. The JSON columns hold the submission and the
measurement whole, so a turn replays without re-planning it. Turns cascade with
their thread: a thread has no meaning without them.

Revision ID: 0004_server_thread
Revises: 0003_drop_anonymous_client
Create Date: 2026-10-12

"""

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
    # The key already serves `WHERE thread_id = ?`; the index makes the
    # `ORDER BY revision DESC LIMIT 1` of `latest` a seek rather than a sort.
    op.execute(
        "CREATE INDEX IF NOT EXISTS turns_thread_revision_ix ON turns (thread_id, revision DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS turns_thread_revision_ix")
    op.execute("DROP TABLE IF EXISTS turns")
    op.execute("DROP TABLE IF EXISTS threads")
