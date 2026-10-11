"""the anonymous client and its saved routes leave the schema

``clients``, ``client_preferences`` and ``saved_routes`` existed for a browser
that had not signed in yet: a signing-in account "adopted" that client so the
client's saved routes became the account's. The product is sign-in only — the
panel route guard sends an anonymous visitor to ``/login``, and every endpoint
the app calls goes through the session — so the tables and the
``users.client_id`` link hold nothing the application can reach, and this
revision removes them.

Order matters: ``users.client_id`` references ``clients``, and so do the two
feature tables, so the column goes first and ``clients`` last. Dropping the
column takes its own unique constraint and foreign key with it.

Revision ID: 0003_drop_anonymous_client
Revises: 0002_taxonomy_itineraries
Create Date: 2026-10-11

"""

from __future__ import annotations

from alembic import op

revision = "0003_drop_anonymous_client"
down_revision = "0002_taxonomy_itineraries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Column first: it carries the FK (and the unique) back into `clients`.
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS client_id")
    op.execute("DROP TABLE IF EXISTS saved_routes")
    op.execute("DROP TABLE IF EXISTS client_preferences")
    op.execute("DROP TABLE IF EXISTS clients")


def downgrade() -> None:
    # The reverse of the baseline's own DDL, so a downgrade lands on the same
    # schema 0001 built. Rows are not restored — only the shape.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS clients (
            id           UUID PRIMARY KEY,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS client_id UUID UNIQUE
            REFERENCES clients (id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS client_preferences (
            client_id                UUID PRIMARY KEY
                                     REFERENCES clients (id) ON DELETE CASCADE,
            transport                TEXT
                                     CHECK (transport IS NULL OR
                                            transport IN ('pedestrian', 'bicycle', 'auto')),
            time_budget_minutes      INTEGER CHECK (time_budget_minutes IS NULL OR
                                                    time_budget_minutes >= 0),
            party_adults             INTEGER CHECK (party_adults IS NULL OR party_adults >= 0),
            party_children           INTEGER CHECK (party_children IS NULL OR party_children >= 0),
            interests                TEXT[],
            language                 TEXT
                                     CHECK (language IS NULL OR language IN ('ru', 'en')),
            visit_minutes_by_category JSONB,
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_routes (
            id              UUID PRIMARY KEY,
            client_id       UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
            name            TEXT,
            query           TEXT NOT NULL CHECK (length(btrim(query)) > 0),
            plan            JSONB NOT NULL,
            visit_overrides JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS saved_routes_client_created_ix
            ON saved_routes (client_id, created_at DESC)
        """
    )
