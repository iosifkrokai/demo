"""Drop columns and indexes no code reads."""

from __future__ import annotations

from alembic import op

revision = "0008_schema_cleanup"
down_revision = "0007_taxonomy_highlight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE places DROP COLUMN IF EXISTS description")
    op.execute("ALTER TABLE place_sources DROP COLUMN IF EXISTS verified_at")
    op.execute("ALTER TABLE areas DROP COLUMN IF EXISTS note")
    op.execute("DROP INDEX IF EXISTS categories_role_ix")
    op.execute("DROP INDEX IF EXISTS user_sessions_expires_ix")
    op.execute("DROP INDEX IF EXISTS turns_thread_revision_ix")


def downgrade() -> None:
    op.execute("ALTER TABLE places ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE place_sources ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ")
    op.execute("ALTER TABLE areas ADD COLUMN IF NOT EXISTS note TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS categories_role_ix ON categories (role)")
    op.execute("CREATE INDEX IF NOT EXISTS user_sessions_expires_ix ON user_sessions (expires_at)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS turns_thread_revision_ix ON turns (thread_id, revision DESC)"
    )
