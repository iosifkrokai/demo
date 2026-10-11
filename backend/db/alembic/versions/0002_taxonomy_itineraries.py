"""taxonomy, itineraries, and the area registry's missing columns

Three things that lived in files until now, and move into the database so the
service stops reading datasets at runtime:

  * ``categories``      — the taxonomy, whose row shape `db/models/category.py`
                          already described but which had no table.
  * ``itineraries`` (+``itinerary_stops``) — the curated walks the catalogue
                          endpoint serves, previously read from
                          ``data/itineraries.json``.
  * ``areas.note`` / ``areas.is_project_area`` — the two facts ``areas.json``
                          carries that the `areas` table had nowhere to put.

The project-area row is keyed ``grodno-oblast`` (the slug ``areas.json`` marks
``is_project_area``), not the ``grodno-voblast`` the seed used to invent from an
English alias; the seed is corrected alongside this revision.

Revision ID: 0002_taxonomy_itineraries
Revises: 0001_baseline
Create Date: 2026-10-11

"""

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

    # The two facts areas.json carries that the table had no home for. Both are
    # nullable/defaulted so an existing row needs no backfill.
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
