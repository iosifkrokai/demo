-- 0004_places_taxonomy.sql: taxonomy support for the canonical place model.
--
-- Adds what spec section 5 asks for on top of the existing `places` table:
--   * place_aliases  — RU/EN alternative names for grounding/resolution
--   * place_sources  — provenance keyed on the natural (provider, external_id)
--   * areas          — named territories (project area, districts) with aliases
--   * places.category_source — which writer owns a row's category, so the
--     hand-curated CSV can never be overwritten by automatic classification.
--
-- Idempotent: every statement is IF NOT EXISTS / CREATE OR REPLACE / DROP+CREATE,
-- so the file can be re-applied to a live DB and re-run after init.sql.
-- Mirrors db/init.sql for fresh installs; run AFTER init.sql on a new volume.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── places.category_source ───────────────────────────────────────────────────
-- 'curated'  : category comes from data/places_curated.csv (hand-labelled)
-- 'dataset'  : category comes from the hand-authored city/region CSVs
-- 'auto'     : category comes from OSM tag mapping or automatic classification
-- Only 'auto' rows may be reclassified; see the guard trigger below.
ALTER TABLE places ADD COLUMN IF NOT EXISTS category_source TEXT NOT NULL DEFAULT 'auto';

-- ── curated-category guard ───────────────────────────────────────────────────
-- Defence in depth for the "curated wins" rule (spec 6.3). Any UPDATE that
-- changes `category` on a curated/dataset row is reverted unless the writer
-- opted in for the current transaction:
--     SELECT set_config('grodno.allow_curated_category_change', 'on', true);
-- scripts/seed_all.py is the sanctioned writer and opts in for its whole apply
-- transaction (its own SQL still refuses to let an automatic writer change a
-- protected row). scripts/enrich_places.py never opts in and only selects
-- 'auto' rows, so automatic classification cannot clobber curated data.
CREATE OR REPLACE FUNCTION places_guard_curated_category() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.category_source IN ('curated', 'dataset')
       AND NEW.category IS DISTINCT FROM OLD.category
       AND COALESCE(current_setting('grodno.allow_curated_category_change', true), 'off') <> 'on'
    THEN
        NEW.category := OLD.category;   -- curated wins; the write is ignored
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS places_guard_curated_category ON places;
CREATE TRIGGER places_guard_curated_category
    BEFORE UPDATE OF category ON places
    FOR EACH ROW EXECUTE FUNCTION places_guard_curated_category();

-- ── place_aliases ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS place_aliases (
    id         SERIAL PRIMARY KEY,
    place_id   INTEGER NOT NULL REFERENCES places (id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,
    locale     TEXT NOT NULL CHECK (locale IN ('ru', 'en')),
    source     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (length(btrim(alias)) > 0)
);

-- Natural key: one alias string per place per locale (case-insensitive).
CREATE UNIQUE INDEX IF NOT EXISTS place_aliases_uniq
    ON place_aliases (place_id, lower(alias), locale);
CREATE INDEX IF NOT EXISTS place_aliases_place_ix ON place_aliases (place_id);
CREATE INDEX IF NOT EXISTS place_aliases_alias_trgm
    ON place_aliases USING GIN (alias gin_trgm_ops);

-- ── place_sources ────────────────────────────────────────────────────────────
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
);

-- Natural key: a provider's own identifier for an object.
CREATE UNIQUE INDEX IF NOT EXISTS place_sources_uniq
    ON place_sources (provider, external_id);
CREATE INDEX IF NOT EXISTS place_sources_place_ix ON place_sources (place_id);

-- ── areas ────────────────────────────────────────────────────────────────────
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
);

CREATE INDEX IF NOT EXISTS areas_geom_gix ON areas USING GIST (geom);
CREATE INDEX IF NOT EXISTS areas_name_ru_trgm ON areas USING GIN (name_ru gin_trgm_ops);
CREATE INDEX IF NOT EXISTS areas_kind_ix ON areas (kind);
