-- init.sql: extensions + schema. Loaded by the official postgres image on first start.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS places (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  description   TEXT,
  category      TEXT,
  lat           DOUBLE PRECISION NOT NULL,
  lon           DOUBLE PRECISION NOT NULL,
  geom          GEOGRAPHY(POINT, 4326) GENERATED ALWAYS AS
                  (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED,
  embedding     VECTOR(384),
  source_url    TEXT UNIQUE NOT NULL,
  photo_url     TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS places_geom_gix   ON places USING GIST (geom);
CREATE INDEX IF NOT EXISTS places_embed_hnsw ON places USING hnsw (embedding vector_cosine_ops);
