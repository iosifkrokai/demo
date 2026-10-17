"""Add categories.highlight."""

from __future__ import annotations

from alembic import op

revision = "0007_taxonomy_highlight"
down_revision = "0006_place_is_free"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE categories ADD COLUMN IF NOT EXISTS highlight BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        """
        UPDATE categories SET highlight = true
         WHERE role = 'service'
           AND code IN ('кафе', 'ресторан', 'туалет', 'гостиница', 'остановка транспорта')
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE categories DROP COLUMN IF EXISTS highlight")
