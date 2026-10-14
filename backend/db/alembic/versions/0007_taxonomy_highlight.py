"""whether a service category is highlighted beside a route by default

The default highlight was "every role=service category", so seeding stairways would
have shown 1500 stairs instead of cafés and toilets (roadmap item 7). This column
names the categories the default shows; the coordinates live in `taxonomy.csv`, which
the seed projects, and the five services the catalogue already shipped with are set
here so a live database needs no re-seed.

Revision ID: 0007_taxonomy_highlight
Revises: 0006_place_is_free
Create Date: 2026-10-14

"""

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
