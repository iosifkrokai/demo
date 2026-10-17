"""Taxonomy, itineraries, and the area registry's two missing columns."""

from __future__ import annotations

from alembic import op

revision = "0002_taxonomy_itineraries"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            code          TEXT PRIMARY KEY,
            ru            TEXT NOT NULL,
            en            TEXT NOT NULL,
            role          TEXT NOT NULL CHECK (role IN ('sight', 'service')),
            osm_tags      TEXT[] NOT NULL DEFAULT '{}',
            visit_minutes INT NOT NULL CHECK (visit_minutes > 0),
            aliases_ru    TEXT[] NOT NULL DEFAULT '{}',
            aliases_en    TEXT[] NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS categories_role_ix ON categories (role)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS itineraries (
            id        TEXT PRIMARY KEY,
            title     TEXT NOT NULL,
            blurb     TEXT NOT NULL DEFAULT '',
            transport TEXT NOT NULL DEFAULT 'pedestrian',
            position  INT NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS itinerary_stops (
            itinerary_id TEXT NOT NULL
                REFERENCES itineraries (id) ON DELETE CASCADE,
            ordinal      INT NOT NULL,
            source_url   TEXT NOT NULL,
            PRIMARY KEY (itinerary_id, ordinal)
        )
        """
    )

    op.execute("ALTER TABLE areas ADD COLUMN IF NOT EXISTS note TEXT")
    op.execute(
        "ALTER TABLE areas ADD COLUMN IF NOT EXISTS is_project_area BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE areas DROP COLUMN IF EXISTS is_project_area")
    op.execute("ALTER TABLE areas DROP COLUMN IF EXISTS note")
    op.execute("DROP TABLE IF EXISTS itinerary_stops")
    op.execute("DROP TABLE IF EXISTS itineraries")
    op.execute("DROP INDEX IF EXISTS categories_role_ix")
    op.execute("DROP TABLE IF EXISTS categories")
