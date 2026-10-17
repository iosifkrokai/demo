"""The baseline schema: extensions, tables, indexes, and the curated-category guard."""

from __future__ import annotations

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS places (
          id            SERIAL PRIMARY KEY,
          name          TEXT NOT NULL,
          description   TEXT,
          category      TEXT,
          blurb         TEXT,
          fun_fact      TEXT,
          fun_facts     TEXT,
          links         TEXT,
          opening_hours TEXT,
          ticket_price  TEXT,
          visit_minutes INT,
          district      TEXT,
          town          TEXT,
          lat           DOUBLE PRECISION NOT NULL,
          lon           DOUBLE PRECISION NOT NULL,
          geom          GEOGRAPHY(POINT, 4326) GENERATED ALWAYS AS
                          (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED,
          embedding     VECTOR(384),
          source_url    TEXT UNIQUE NOT NULL,
          photo_url     TEXT,
          photo_author  TEXT,
          photo_license TEXT,
          photo_source  TEXT,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS places_geom_gix   ON places USING GIST (geom)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS places_embed_hnsw "
        "ON places USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS places_district_ix ON places (district)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS places_name_trgm   ON places USING GIN (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS places_town_trgm   ON places USING GIN (town gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS places_district_trgm "
        "ON places USING GIN (district gin_trgm_ops)"
    )

    op.execute(
        "ALTER TABLE places ADD COLUMN IF NOT EXISTS category_source TEXT NOT NULL DEFAULT 'auto'"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION places_guard_curated_category() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.category_source IN ('curated', 'dataset')
               AND NEW.category IS DISTINCT FROM OLD.category
               AND COALESCE(current_setting('grodno.allow_curated_category_change', true), 'off') <> 'on'
            THEN
                NEW.category := OLD.category;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS places_guard_curated_category ON places")
    op.execute(
        """
        CREATE TRIGGER places_guard_curated_category
            BEFORE UPDATE OF category ON places
            FOR EACH ROW EXECUTE FUNCTION places_guard_curated_category()
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS place_aliases (
            id         SERIAL PRIMARY KEY,
            place_id   INTEGER NOT NULL REFERENCES places (id) ON DELETE CASCADE,
            alias      TEXT NOT NULL,
            locale     TEXT NOT NULL CHECK (locale IN ('ru', 'en')),
            source     TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (length(btrim(alias)) > 0)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS place_aliases_uniq
            ON place_aliases (place_id, lower(alias), locale)
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS place_aliases_place_ix ON place_aliases (place_id)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS place_aliases_alias_trgm
            ON place_aliases USING GIN (alias gin_trgm_ops)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS place_sources (
            id          SERIAL PRIMARY KEY,
            place_id    INTEGER NOT NULL REFERENCES places (id) ON DELETE CASCADE,
            provider    TEXT NOT NULL,
            external_id TEXT NOT NULL,
            url         TEXT,
            license     TEXT,
            fetched_at  TIMESTAMPTZ,
            verified_at TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS place_sources_uniq
            ON place_sources (provider, external_id)
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS place_sources_place_ix ON place_sources (place_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS areas (
            id         SERIAL PRIMARY KEY,
            code       TEXT NOT NULL UNIQUE,
            name_ru    TEXT NOT NULL,
            name_en    TEXT,
            aliases    TEXT[] NOT NULL DEFAULT '{}',
            kind       TEXT NOT NULL DEFAULT 'district',
            source     TEXT,
            license    TEXT,
            geom       GEOGRAPHY(MULTIPOLYGON, 4326),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS areas_geom_gix ON areas USING GIST (geom)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS areas_name_ru_trgm ON areas USING GIN (name_ru gin_trgm_ops)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS areas_kind_ix ON areas (kind)")

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

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            UUID PRIMARY KEY,
            email         CITEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name  TEXT,
            role          TEXT NOT NULL DEFAULT 'user'
                          CHECK (role IN ('user', 'admin')),
            client_id     UUID UNIQUE REFERENCES clients (id) ON DELETE SET NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS users_role_ix ON users (role)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            token_hash   TEXT PRIMARY KEY,
            user_id      UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at   TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS user_sessions_user_ix ON user_sessions (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS user_sessions_expires_ix ON user_sessions (expires_at)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS visited_places (
            user_id    UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            place_id   INTEGER NOT NULL REFERENCES places (id) ON DELETE CASCADE,
            visited_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, place_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS visited_places_user_ix
            ON visited_places (user_id, visited_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS visited_places")
    op.execute("DROP TABLE IF EXISTS user_sessions")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS saved_routes")
    op.execute("DROP TABLE IF EXISTS client_preferences")
    op.execute("DROP TABLE IF EXISTS clients")
    op.execute("DROP TABLE IF EXISTS areas")
    op.execute("DROP TABLE IF EXISTS place_sources")
    op.execute("DROP TABLE IF EXISTS place_aliases")
    op.execute("DROP TRIGGER IF EXISTS places_guard_curated_category ON places")
    op.execute("DROP FUNCTION IF EXISTS places_guard_curated_category()")
    op.execute("DROP TABLE IF EXISTS places")
