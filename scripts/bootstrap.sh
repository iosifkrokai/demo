#!/usr/bin/env bash
#
# Bring the whole Grodno stack up on a fresh machine.
#
#   bash scripts/bootstrap.sh --check     # verify prerequisites, change nothing
#   bash scripts/bootstrap.sh             # full flow: compose + seeds
#   bash scripts/bootstrap.sh --skip-osm  # curated + POI seeds only (faster)
#
# Steps: prerequisites -> frontend/.env -> uv sync -> db+valhalla -> frontend ->
#        seed curated places -> OSM ingest -> POI (cafes/toilets/hotels) ingest.
# The agent is NOT started here on purpose: run it from the checkout so code
# edits need no image rebuild (the command is printed at the end).
#
# Idempotent: every ingest upserts on source_url, so re-running changes nothing.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_OSM=0
SKIP_POI=0
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --skip-osm) SKIP_OSM=1 ;;
        --skip-poi) SKIP_POI=1 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown flag: $arg (try --help)" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }

# ── 1. prerequisites ───────────────────────────────────────────────────────────
say "prerequisites"
command -v docker >/dev/null || { echo "docker is missing" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose plugin is missing" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "the docker daemon is not running" >&2; exit 1; }
command -v uv >/dev/null || { echo "uv is missing: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is missing" >&2; exit 1; }
echo "docker, docker compose, uv, curl: ok"

# ── 2. .env (one file for backend + frontend) ──────────────────────────────────
say ".env"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "created .env from .env.example — backend and frontend read this single file"
else
    echo ".env already exists"
fi

# Everything below (and the agent you start later) reads this one file.
set -a
# shellcheck disable=SC1091
. ./.env
set +a

# Seeding needs these; the agent itself degrades to keyword-only without a key.
DATABASE_URL="${DATABASE_URL:-postgresql://grodno:grodno@localhost:5432/grodno}"
EMBED=1
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    EMBED=0
    warn "OPENROUTER_API_KEY is not set: places will be seeded WITHOUT embeddings
         (keyword-only retrieval until you re-run the seeds with the key set)"
fi
export DATABASE_URL

if [ "$CHECK_ONLY" = 1 ]; then
    say "plan (nothing was changed)"
    cat <<EOF
db + valhalla ............. docker compose up -d db valhalla   (valhalla builds
                            tiles from the Belarus PBF on first start, 5-10 min)
frontend .................. docker compose up -d --build frontend
uv sync ................... cd backend && uv sync --group dev
seed curated places ....... scripts/seed_region.py $([ "$EMBED" = 1 ] && echo --embed)
ingest OSM POIs ........... scripts/ingest_osm.py && scripts/load_osm.py $([ "$EMBED" = 1 ] || echo --no-embed)
ingest cafes/toilets ...... scripts/ingest_poi.py $([ "$EMBED" = 1 ] || echo --no-embed)
agent ..................... .venv/bin/python -m uvicorn agent.main:app --port 8080
EOF
    exit 0
fi

# ── 3. python env ──────────────────────────────────────────────────────────────
say "python env (uv sync)"
(cd backend && uv sync --group dev)

# ── 4. db + valhalla ──────────────────────────────────────────────────────────
say "db + valhalla (tiles build on first start)"
docker compose up -d db valhalla
printf 'waiting for valhalla'
until curl -fsS http://localhost:8002/status >/dev/null 2>&1; do
    printf '.'
    sleep 5
done
echo " ready"

# ── 5. frontend ───────────────────────────────────────────────────────────────
say "frontend"
docker compose up -d --build frontend
echo "UI: http://localhost/  (nginx proxies /routes/* -> agent, /status -> valhalla)"

# ── 6. seed ───────────────────────────────────────────────────────────────────
say "seed: curated places"
(cd backend && EMBED_FLAG=(); [ "$EMBED" = 1 ] && EMBED_FLAG=(--embed)
 .venv/bin/python scripts/seed_region.py "${EMBED_FLAG[@]+"${EMBED_FLAG[@]}"}")

if [ "$SKIP_OSM" = 0 ]; then
    say "seed: OSM places for the voblast (~3 min of Overpass)"
    (cd backend &&
        .venv/bin/python scripts/ingest_osm.py &&
        NOEMBED_FLAG=(); [ "$EMBED" = 0 ] && NOEMBED_FLAG=(--no-embed)
        .venv/bin/python scripts/load_osm.py "${NOEMBED_FLAG[@]+"${NOEMBED_FLAG[@]}"}")
fi

if [ "$SKIP_POI" = 0 ]; then
    say "seed: cafes / restaurants / toilets / hotels"
    (cd backend &&
        NOEMBED_FLAG=(); [ "$EMBED" = 0 ] && NOEMBED_FLAG=(--no-embed)
        .venv/bin/python scripts/ingest_poi.py "${NOEMBED_FLAG[@]+"${NOEMBED_FLAG[@]}"}")
fi

# ── 7. count + how to run the agent ───────────────────────────────────────────
say "database"
docker exec grodno-db psql -U grodno -d grodno -tAc \
    "SELECT count(*) || ' places, ' || count(embedding) || ' embedded' FROM places;"

say "next: run the agent from the checkout"
cat <<EOF
cd backend
set -a; . ../.env; set +a          # the single project env file
export VALHALLA_URL='http://localhost:8002'
export DATABASE_URL="postgresql://\${POSTGRES_USER:-grodno}:\${POSTGRES_PASSWORD:-grodno}@localhost:5432/\${POSTGRES_DB:-grodno}"
.venv/bin/python -m uvicorn agent.main:app --host 0.0.0.0 --port 8080

# health, then a smoke route:
curl -fsS localhost:8080/health | head -c 200
curl -sX POST localhost:8080/routes/generate -H 'content-type: application/json' \\
     -d '{"query":"Хочу погулять по замкам Гродно","time_budget_minutes":120}'
EOF
