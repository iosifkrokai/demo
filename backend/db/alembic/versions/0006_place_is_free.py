"""Add places.is_free."""

from __future__ import annotations

from alembic import op

revision = "0006_place_is_free"
down_revision = "0005_data_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE places ADD COLUMN IF NOT EXISTS is_free BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE places DROP COLUMN IF EXISTS is_free")
