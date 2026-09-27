-- init.sql: extensions + schema. Loaded by the official postgres image on first
-- start. For an existing volume apply db/migrations/*.sql instead (idempotent).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS places (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  description   TEXT,
  category      TEXT,
  blurb         TEXT,
  -- Short "did you know" line shown next to the marker on the map.
  fun_fact      TEXT,
  -- Additional facts (JSON array of strings).
  fun_facts     TEXT,
  -- JSON array [{title, url}] of related links.
  links         TEXT,
  -- Visitor info (region dataset; see specs/001-grodno-region-coverage).
  opening_hours TEXT,
  ticket_price  TEXT,
  visit_minutes INT,
  district      TEXT,
  town          TEXT,
  lat           DOUBLE PRECISION NOT NULL,
  lon           DOUBLE PRECISION NOT NULL,
  geom          GEOGRAPHY(POINT, 4326) GENERATED ALWAYS AS
                  (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED,
  -- embedding dim MUST match the model in scripts/enrich_places.py and agent/main.py.
  -- Default: OpenRouter text-embedding-3-small (1536-d).
  embedding     VECTOR(1536),
  source_url    TEXT UNIQUE NOT NULL,
  -- A photo is only ever stored with its attribution: Wikimedia files are
  -- licensed, and "photo_url set, author NULL" would be a licence violation
  -- waiting to happen. All four come from one resolution pass, never guessed.
  photo_url     TEXT,
  photo_author  TEXT,
  photo_license TEXT,
  photo_source  TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS places_geom_gix   ON places USING GIST (geom);
CREATE INDEX IF NOT EXISTS places_embed_hnsw ON places USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS places_district_ix ON places (district);
CREATE INDEX IF NOT EXISTS places_name_trgm   ON places USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS places_town_trgm   ON places USING GIN (town gin_trgm_ops);
CREATE INDEX IF NOT EXISTS places_district_trgm ON places USING GIN (district gin_trgm_ops);