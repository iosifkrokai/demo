"""the free-admission fact, which `ticket_price` only ever implied

`ticket_price` is prose in two vocabularies — OSM's `fee`/`charge` tag, and a human
sentence on the hand-authored rows — so a plan verdict about cost had to read the
string and kept a `—`, which reads as a value though it means nothing. This column
holds the reading itself: whether admission is free. Nullable on purpose, and `NULL`
is the answer for every row whose price says nothing — the fifteen dashes included —
because an unknown fee is not a free one.

Revision ID: 0006_place_is_free
Revises: 0005_data_version
Create Date: 2026-10-14

"""

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
