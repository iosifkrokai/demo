-- 0007_rename_transit_stop_category.sql: rename the transit-stop category code.
--
-- Background: the canonical code in data/taxonomy.csv was changed from
-- "остановка" → "остановка транспорта" (role=service, OSM tags for
-- bus_stop / tram_stop / public_transport=platform).  The word "остановка"
-- in a tourist query ("…с обязательной остановкой у Фарного костёла")
-- was being resolved as a bus stop and polluting the POI search.
--
-- What this file does: updates places.category from 'остановка' to
-- 'остановка транспорта' for the ~3.4 k rows that were seeded from OSM.
--
-- Idempotent: the WHERE clause includes "category = 'остановка'".  If the
-- rename was already applied, zero rows match and the statement is a no-op.
-- Safe to re-run after init.sql or after a partial re-seeding.
--
-- Why ONLY category_source = 'auto':
--   The places_guard_curated_category trigger (0004) blocks any UPDATE that
--   changes `category` on a row whose category_source IN ('curated', 'dataset')
--   unless the writer set grodno.allow_curated_category_change = 'on'.
--   Rows with source = 'curated'/'dataset' were written by humans or
--   hand-authored CSVs; their category is already correct and must not be
--   silently changed by a migration.  They don't hold the old "остановка"
--   code anyway, so including them in the WHERE would be a no-op — but we
--   exclude them explicitly so that:
--     (a) the trigger never fires inside this migration's transaction, and
--     (b) the intent is obvious to anyone reading the SQL.
--   OSM tags in source_url are NOT checked here because ingest_poi.py is the
--   authoritative source for OSM provenance, and category_source='auto' already
--   means "written by an automatic process from OSM".  Adding a redundant tag
--   check would only make the query longer and harder to reason about.
--
-- Mirrors db/init.sql: no change needed in init.sql because init.sql defines
-- the schema (columns, constraints, functions, triggers) but does not
-- hardcode any category strings — category values are written at seed time.
-- Run after 0004 and 0006 on an existing volume:
--   psql "$DATABASE_URL" -f db/migrations/0007_rename_transit_stop_category.sql

-- ── Rename transit-stop category for auto rows only ───────────────────────────
UPDATE places
SET    category = 'остановка транспорта'
WHERE  category       = 'остановка'
  AND  category_source = 'auto';
