-- Run: docker exec grodno-db psql -U grodno -d grodno -f /docker-entrypoint-initdb.d/migrate_add_facts.sql
-- Or directly: psql "$DATABASE_URL" -f db/migrate_add_facts.sql

ALTER TABLE places
ADD COLUMN IF NOT EXISTS fun_facts TEXT,
ADD COLUMN IF NOT EXISTS links TEXT;
