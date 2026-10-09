-- 0005_clients.sql: the anonymous client entity (spec 003).
--
-- Backs three things a browser currently keeps in localStorage and loses when
-- it changes machine or clears storage: saved routes, the tourist's
-- preferences, and the "my usual pace" per category.
--
--   * clients              — one anonymous row per X-Client-Id (UUIDv4); no
--                            login, no password, nothing that identifies a
--                            person. created_at/last_seen_at only.
--   * client_preferences   — one row per client, partial: every field may be
--                            NULL, and NULL means "not stated", never a
--                            guessed number.
--   * saved_routes         — the checked plan stored verbatim, so a restored
--                            route is byte-for-byte what the tourist saw.
--                            Never rebuilt at read time.
--
-- Idempotent and non-destructive: CREATE TABLE / CREATE INDEX IF NOT EXISTS,
-- so the file can be re-applied to a live DB without touching existing rows.
-- Run after init.sql on a fresh volume, or on any live volume that predates
-- this table.  Mirrors db/init.sql for fresh installs.
--   docker exec grodno-db psql -U grodno -d grodno -f /path/to/0005_clients.sql
--   # or: psql "$DATABASE_URL" -f db/migrations/0005_clients.sql
--
-- Deletion is a single cascade: DELETE FROM clients removes its preferences
-- and its routes through the FKs below (spec 5, "удалить мои данные").

-- clients
-- The id is minted by the browser (crypto.randomUUID) and sent as X-Client-Id;
-- the server inserts the row on first sight. No column here can identify a
-- person (§5).
CREATE TABLE IF NOT EXISTS clients (
    id           UUID PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- client_preferences
-- One row per client. All value columns are nullable on purpose: NULL = "the
-- tourist did not state it". A missing row and an all-NULL row mean the same
-- thing to the API (nothing saved yet).
CREATE TABLE IF NOT EXISTS client_preferences (
    client_id                UUID PRIMARY KEY
                             REFERENCES clients (id) ON DELETE CASCADE,
    -- 'pedestrian' | 'bicycle' | 'auto' | NULL
    transport                TEXT
                             CHECK (transport IS NULL OR
                                    transport IN ('pedestrian', 'bicycle', 'auto')),
    time_budget_minutes      INTEGER CHECK (time_budget_minutes IS NULL OR
                                            time_budget_minutes >= 0),
    -- NULL = not stated, never an invented party size.
    party_adults             INTEGER CHECK (party_adults IS NULL OR party_adults >= 0),
    party_children           INTEGER CHECK (party_children IS NULL OR party_children >= 0),
    -- Canonical taxonomy codes (data/taxonomy.csv), e.g. {замок, костёл}.
    interests                TEXT[],
    -- 'ru' | 'en' | NULL
    language                 TEXT
                             CHECK (language IS NULL OR language IN ('ru', 'en')),
    -- {code: minutes} — "my usual pace". NULL means "no overrides stated".
    visit_minutes_by_category JSONB,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- saved_routes
-- `plan` holds the checked TripPlan exactly as shown (points, geometry,
-- manoeuvres, requirements, costing, summary). It is stored, not rebuilt: a
-- rebuilt route is not allowed to silently differ from what the tourist saw.
CREATE TABLE IF NOT EXISTS saved_routes (
    id              UUID PRIMARY KEY,
    client_id       UUID NOT NULL REFERENCES clients (id) ON DELETE CASCADE,
    -- As the tourist named it; may be empty/NULL ("untitled").
    name            TEXT,
    -- The original query: the route is restored from this + the stored plan.
    query           TEXT NOT NULL CHECK (length(btrim(query)) > 0),
    plan            JSONB NOT NULL,
    -- {point id: minutes} — visit time the tourist changed, if any.
    visit_overrides JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The list a tourist sees is newest-first, so index for exactly that.
CREATE INDEX IF NOT EXISTS saved_routes_client_created_ix
    ON saved_routes (client_id, created_at DESC);
