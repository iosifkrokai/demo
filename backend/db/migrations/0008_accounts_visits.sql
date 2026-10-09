-- 0008_accounts_visits.sql: accounts + roles, server-side visits, admin surface
-- (spec 005). Extends spec 003's anonymous client — it is NOT replaced: when an
-- account is created or logs in, the browser's X-Client-Id is *adopted* by the
-- account (users.client_id), so its saved routes and preferences stay reachable.
--
--   * users            — one row per account. email is CITEXT (case-insensitive
--                        unique). password_hash is a self-describing scrypt string;
--                        the plaintext never reaches the database.
--   * user_sessions    — one row per live session; the table holds sha256(token),
--                        never the token itself, so a database leak cannot log in.
--   * visited_places   — the tourist's durable "I have been here" registry, per
--                        account. Distinct from the walk progress in
--                        localStorage: that is the state of one walk, this is a
--                        fact about the tourist (spec 003 §4).
--
-- Idempotent and non-destructive: CREATE TABLE / INDEX IF NOT EXISTS, so it can be
-- re-applied to a live DB. Run after init.sql on a fresh volume, or on any live
-- volume that predates these tables. Mirrors db/init.sql for fresh installs.
--   docker exec grodno-db psql -U grodno -d grodno -f /path/to/0008_accounts_visits.sql
--   # or: psql "$DATABASE_URL" -f db/migrations/0008_accounts_visits.sql

-- Case-insensitive email needs the citext type; the official postgres image
-- ships it in the contrib extensions the image's superuser can enable.
CREATE EXTENSION IF NOT EXISTS citext;

-- users
-- role is the only authority the API reads; a missing role means 'user' (the
-- column default), never an implied admin.
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY,
    email         CITEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'user'
                  CHECK (role IN ('user', 'admin')),
    -- The anonymous client this account adopted. ON DELETE SET NULL: deleting a
    -- client (spec 003 «удалить мои данные») must not delete the account, only
    -- unlink it.
    client_id     UUID UNIQUE REFERENCES clients (id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS users_role_ix ON users (role);

-- user_sessions
-- The token is minted by the server (secrets.token_urlsafe) and sent to the
-- browser in an HttpOnly cookie; only its sha256 lives here. expires_at is
-- absolute, so a session is looked up by (hash, now) < expires_at and a stale row
-- is simply not found.
CREATE TABLE IF NOT EXISTS user_sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS user_sessions_user_ix ON user_sessions (user_id);
CREATE INDEX IF NOT EXISTS user_sessions_expires_ix ON user_sessions (expires_at);

-- visited_places
-- Composite PK makes marking idempotent: a repeat PUT is a no-op upsert, never a
-- duplicate. Deleting a place or an account cascades the mark away.
CREATE TABLE IF NOT EXISTS visited_places (
    user_id    UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    place_id   INTEGER NOT NULL REFERENCES places (id) ON DELETE CASCADE,
    visited_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, place_id)
);

CREATE INDEX IF NOT EXISTS visited_places_user_ix
    ON visited_places (user_id, visited_at DESC);
