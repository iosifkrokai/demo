-- 0009_local_embeddings_384.sql: retrieval moved to a local CPU model.
--
-- Embeddings are no longer OpenRouter's text-embedding-3-small (1536-d): they
-- are computed locally by agent/embeddings.py with intfloat/multilingual-e5-small
-- (384-d) through fastembed/ONNX. The column width must match the model, and a
-- vector(1536) column cannot be cast to vector(384), so every existing vector is
-- invalidated and must be recomputed.
--
-- Consequences, stated plainly: after this migration every row has
-- embedding IS NULL, so vector search returns nothing until the seed re-embeds
-- (`python -m seed`, i.e. `docker compose --profile seed run --rm seed`).
-- Keyword-only retrieval is the honest interim state, and the seed's coverage
-- report shows it as `pending_embeddings`.
--
-- Idempotent and safe to re-run: it only rewrites the column when it is not
-- already 384-d. Mirrors db/init.sql for fresh installs.
--   psql "$DATABASE_URL" -f db/migrations/0009_local_embeddings_384.sql

DO $$
DECLARE
    current_dim int;
BEGIN
    SELECT atttypmod INTO current_dim
      FROM pg_attribute
     WHERE attrelid = 'places'::regclass AND attname = 'embedding';
    -- pgvector stores the dimension in atttypmod directly (384, not 384+4).
    IF current_dim = 384 THEN
        RETURN;
    END IF;

    DROP INDEX IF EXISTS places_embed_hnsw;
    ALTER TABLE places ALTER COLUMN embedding TYPE vector(384) USING NULL::vector(384);
END $$;

CREATE INDEX IF NOT EXISTS places_embed_hnsw
    ON places USING hnsw (embedding vector_cosine_ops);
