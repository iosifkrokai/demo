"""The data-version row the seed stamps."""

from __future__ import annotations

from alembic import op

revision = "0005_data_version"
down_revision = "0004_server_thread"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_versions (
            name       TEXT PRIMARY KEY,
            version    TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_versions")
