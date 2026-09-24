-- init.sql: extensions + schema. Loaded by the official postgres image on first start.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS places (
  id            SERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  description   TEXT,
  category      TEXT,
  blurb         TEXT,
  -- Short "did you know" line shown next to the marker on the map. Curated in
  -- data/places_curated.csv (column 5) and applied by scripts/apply_curated.py.
  fun_fact      TEXT,
  lat           DOUBLE PRECISION NOT NULL,
  lon           DOUBLE PRECISION NOT NULL,
  geom          GEOGRAPHY(POINT, 4326) GENERATED ALWAYS AS
                  (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED,
  -- embedding dim MUST match the model in scripts/enrich_places.py and agent/main.py.
  -- Default: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384-d, ONNX via fastembed).
  -- If you switch to a different model, also bump this and drop+recreate the index.
  embedding     VECTOR(384),
  source_url    TEXT UNIQUE NOT NULL,
  photo_url     TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS places_geom_gix   ON places USING GIST (geom);
CREATE INDEX IF NOT EXISTS places_embed_hnsw ON places USING hnsw (embedding vector_cosine_ops);
