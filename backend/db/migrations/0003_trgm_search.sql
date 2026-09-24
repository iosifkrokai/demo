-- 0003_trgm_search.sql: fuzzy Russian keyword search.
-- ILIKE fails on Russian inflection ("новогрудка" vs "Новогрудок",
-- "костёлы" vs "костёл"); trigram similarity handles it.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
SET pg_trgm.word_similarity_threshold = 0.45;

CREATE INDEX IF NOT EXISTS places_name_trgm   ON places USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS places_town_trgm   ON places USING GIN (town gin_trgm_ops);
CREATE INDEX IF NOT EXISTS places_district_trgm ON places USING GIN (district gin_trgm_ops);