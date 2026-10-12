"""the data version the seed stamps

The measurement cache is keyed on the stops, the profile and the budget — all of
which describe the *request*, not the rows the walk is measured against. A re-seed
can move a place without the request changing, and every number cached for it goes
stale. This table closes that: `python -m db.seed` writes one row holding the
digest of the committed datasets (`db.seed.version`), and the planner carries it in
the measurement key, so a re-seed misses instead of replaying.

One row, keyed by name (`seed`); `version` changes only when the projected content
does. Read by `db/store/datasets.py`, written by `db/seed/pipeline.py`.

Revision ID: 0005_data_version
Revises: 0004_server_thread
Create Date: 2026-10-12

"""

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
