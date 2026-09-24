-- 0002_region_fields.sql: region coverage fields for places.
-- Idempotent; safe on a live DB. Mirrored into db/init.sql for fresh installs.

-- fun_facts / links existed only in migrate_add_facts.sql for old installs;
-- folded in here so one migration brings any pre-0002 schema up to date.
ALTER TABLE places ADD COLUMN IF NOT EXISTS fun_facts TEXT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS links     TEXT;

ALTER TABLE places ADD COLUMN IF NOT EXISTS opening_hours TEXT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS ticket_price  TEXT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS visit_minutes INT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS district      TEXT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS town          TEXT;

CREATE INDEX IF NOT EXISTS places_district_ix ON places (district);
