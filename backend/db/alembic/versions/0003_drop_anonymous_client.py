"""Drop the anonymous client, its preferences, its saved routes, and users.client_id."""

from __future__ import annotations

from alembic import op

revision = "0003_drop_anonymous_client"
down_revision = "0002_taxonomy_itineraries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS client_id")
    op.execute("DROP TABLE IF EXISTS saved_routes")
    op.execute("DROP TABLE IF EXISTS client_preferences")
    op.execute("DROP TABLE IF EXISTS clients")


def downgrade() -> None:
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
