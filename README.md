# Grodno walking-route POC

Stack: **Valhalla** (routing engine) + **Postgres/PostGIS/pgvector** (places) +
**FastAPI agent** (free-text Russian → pedestrian route) + **Vite webapp** (UI).

## 0. Prereqs (host)

```bash
# macOS
brew install docker docker-compose colima osmium-tool wget uv postgresql@16 jq
# Debian/Ubuntu
sudo apt-get install -y docker.io docker-compose-plugin osmium-tool wget jq postgresql-client
curl -LsSf https://astral.sh/uv/install.sh | sh    # uv
```

For network: outbound HTTPS to `download.geofabrik.de`, `nominatim.openstreetmap.org`,
`tile.openstreetmap.org`, `overpass-api.de`, `openrouter.ai`. **OPENROUTER_API_KEY is
optional** — without it the pipeline degrades to keyword-only retrieval (no embeddings,
no Jev intent/rerank).

## 1. Web-app subdir

The webapp lives in `frontend/` (our customizations on top of valhalla/web-app — sidebar,
waypoints, place cards). Create its `.env` before building the image:

```bash
cat > frontend/.env <<'ENV'
SKIP_PREFLIGHT_CHECK=true
VITE_VALHALLA_URL=http://localhost:8002
VITE_NOMINATIM_URL=https://nominatim.openstreetmap.org
VITE_TILE_SERVER_URL="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
VITE_CENTER_COORDS="53.6772,23.8232"
VITE_DEFAULT_COSTING_MODEL=pedestrian
VITE_CLIENT_ID=grodno-poc
VITE_AGENT_URL=http://localhost:8080
ENV
```

`VITE_*` vars are baked at image build time, so this file MUST exist before `docker compose build`.
`frontend/.dockerignore` must NOT exclude `package-lock.json` — the Dockerfile runs `npm ci`,
which fails without the lockfile in the build context.

## 2. Python env (uv)

```bash
cd backend
uv sync                    # runtime + dev (ruff/pyright/pytest) into backend/.venv
.venv/bin/python -c "from agent.main import app; print('OK')"
```

Pinned Python is `3.12`. `uv` resolves everything in `backend/pyproject.toml`;
there is no `requirements.txt`.

## 3. Bring up Valhalla + Postgres (Docker)

```bash
cd ..   # back to repo root

# 3a. Build & start db + valhalla (valhalla builds tiles on first start, ~5–10 min
#     for the whole Belarus PBF)
docker compose up -d db valhalla

# 3b. Wait for Valhalla readiness
until curl -fsS http://localhost:8002/status >/dev/null; do
    echo "waiting for valhalla..."; sleep 5
done

# 3c. Build & start the webapp
docker compose up -d --build frontend
```

UI: <http://localhost/>.

The **agent service is intentionally commented out** in `docker-compose.yml` —
run it locally via `uvicorn` (step 5) so you can iterate without rebuilding images.

## 4. Seed the DB

```bash
cd backend
export DATABASE_URL=postgresql://grodno:***@localhost:5432/grodno
export OPENROUTER_API_KEY=sk-or-...        # required for embeddings

# 4a. Curated places (city + voblast CSV) + embeddings for every row.
#     Idempotent: upsert keyed on source_url.
.venv/bin/python scripts/seed_region.py            # dry: prints the plan, writes nothing
.venv/bin/python scripts/seed_region.py --embed

# 4b. Optional: widen coverage with OSM POIs (~4.4k rows for the voblast)
.venv/bin/python scripts/ingest_osm.py             # Overpass → data/places_osm_raw.csv (~3 min)
.venv/bin/python scripts/load_osm.py --dry-run     # validate + print the plan
.venv/bin/python scripts/load_osm.py               # upsert + embed
```

`ingest_osm.py` also takes `--input-json <saved Overpass response>` (skip the network),
`--district-mode nominatim` (real reverse geocoding, 1 req/s) and `--limit N`.
`load_osm.py` takes `--dry-run`, `--limit`, `--no-embed`, `--path`.

Spot-check:
```bash
docker exec grodno-db psql -U grodno -d grodno -c "
SELECT count(*) AS n,
       count(embedding) AS with_emb,
       count(blurb) AS with_blurb,
       count(fun_fact) AS with_fact
FROM places;"
```

Legacy one-shot scrapers (`parse_places.py`, `enrich_places.py`, `apply_curated.py`) predate
the `backend/` restructure and are not part of the current seed path.

## 5. Run the agent

```bash
cd backend
export DATABASE_URL=postgresql://grodno:***@localhost:5432/grodno
export VALHALLA_URL=http://localhost:8002
export OPENROUTER_API_KEY=sk-or-...    # optional; without it the agent is keyword-only

.venv/bin/python -m uvicorn agent.main:app --host 0.0.0.0 --port 8080
```

No local models, no first-call download: embeddings run on OpenRouter
(`openai/text-embedding-3-small`) and intent/rerank on the **Jev** typed-decision endpoint
(`POST /api/v1/systemone`, `typesafe/jev-1.13`).

## 6. Smoke tests

```bash
# Health
curl -fsS localhost:8080/health | jq

# Generate a route
curl -sX POST localhost:8080/routes/generate \
    -H 'content-type: application/json' \
    -d '{"query":"Хочу погулять по замкам Гродно","time_budget_minutes":120}' \
    | jq '.points[] | {name, category, fun_fact}'

# Named place + explicit start position (route starts at the given point)
curl -sX POST localhost:8080/routes/generate \
    -H 'content-type: application/json' \
    -d '{"query":"Хочу к Мирскому замку","time_budget_minutes":150,
         "origin":{"lat":53.4510,"lon":26.4722}}' \
    | jq '.summary, (.points[] | .name)'

# Re-route an explicit list
curl -sX POST localhost:8080/routes/reroute \
    -H 'content-type: application/json' \
    -d '{"point_ids":[49,50,51]}' \
    | jq '.summary'
```

## 6a. Golden-set benchmark

`backend/benchmarks/routes/*.json` holds reference walks taken from Wikivoyage
(Старый город, Мир, Новогрудок). The runner drives the live HTTP API and reports
recall@K, precision, Kendall τ, walk-time delta and budget fit:

```bash
cd backend
.venv/bin/python scripts/bench_routes.py --base-url http://localhost:8080
# prints a table and writes backend/benchmarks/report.json + report.md
```

## 7. Dev workflow

```bash
cd backend

# Lint (auto-fix safe issues)
.venv/bin/ruff check --fix .

# Format
.venv/bin/ruff format .

# Type-check (strict-ish, ~5 sec)
.venv/bin/pyright

# Tests
.venv/bin/python -m pytest -q
```

CI equivalent (run before commit):
```bash
cd backend && .venv/bin/ruff check . && .venv/bin/pyright
```

## 8. Configuration

Environment carries secrets and addresses only (`agent/config.py`):
`OPENROUTER_API_KEY`, `DATABASE_URL`, `VALHALLA_URL`, `AGENT_HOST`, `AGENT_PORT`.

Everything else is reviewable code in `agent/constants.py` — models, weights and limits:
`EMBED_MODEL`, `JEV_MODEL`, `RRF_K`, `MMR_LAMBDA`, `RETRIEVAL_POOL_SIZE`,
`RERANK_POOL_SIZE`, `MMR_POOL_SIZE`, `ROUTE_MAX_STOPS`, `GEO_FOCUS_KM`,
`GEO_FOCUS_MAX_KM`, `MAX_WALK_LEG_KM` / `WALK_LEG_BUDGET_SHARE` (walkability),
`NAME_MATCH_MIN_SIM` (named-place → must-visit threshold),
`DUPLICATE_RADIUS_M` (same-POI radius; the curated row and an OSM row for the same
sight must not both appear in one route), budget bounds and the category → visit-minutes
table. Changing one is a code-review decision: edit → tests → commit.

A named-place token becomes a **must-visit** only when it matches a POI *name* above
`NAME_MATCH_MIN_SIM`; a token that only matches a town/district becomes an **area anchor**
for the geo focus instead, so «замки Гродно» is not pinned to one arbitrary Grodno row.

## 9. Troubleshooting

**`503 (valhalla ... sources_to_targets failed after retries)`.** Valhalla 3.5.1 answers 500
`Could not find candidate edge used for label` for matrix shapes with
`len(sources) >= 6 AND len(targets) >= 7`. `agent/valhalla_client.py` chunks around it
(`MATRIX_MAX_SOURCES` / `MATRIX_MAX_TARGETS`) with a per-pair `/route` fallback.

**Route has no polyline / `length_km: null`.** `/route` answers 500
`Could not find candidate edge used for destination label` for some POI coordinates
(e.g. 53.6845,23.8318). Fixed by sending `radius: 100` per location
(`LOCATION_SNAP_RADIUS_M` in `valhalla_client.py`); `search_radius` / `street_side_tolerance`
do NOT help.

**"relation 'places' does not exist".** `db/init.sql` only loads on FIRST start of the `db`
container. With an existing `pgdata` volume, apply `db/migrations/*.sql` and
`db/migrate_add_facts.sql` manually.

**Agent returns `503 UpstreamUnavailable` on every request.** Valhalla tile build didn't
finish or the `pgdata` volume lost embeddings — re-run `scripts/seed_region.py --embed`.
Check `docker logs grodno-valhalla`.

**Keyword-only results (no semantic search).** `OPENROUTER_API_KEY` is missing or the rows
have `embedding IS NULL` — see the spot-check in section 4.

**`ConnectError` on `localhost:8002`.** Valhalla isn't ready. Poll `/status` until 200.

**Overpass 504 on the full-voblast ingest.** `ingest_osm.py` retries. Save one successful
response and iterate with `--input-json <file>`.

## 10. Architecture

```
User → Vite webapp (frontend/) → :8080 /routes/generate
                                    ↓
                       agent.main → agent.planner.Pipeline
                                    ↓
  ┌─ preprocess ─ intent (Jev typed decisions) ─ resolve ─┐
  │                                                       │
  ├─ retrieve (vector + keyword + must-visit, RRF) ─ rerank (Jev scores)
  │                                                       │
  ├─ geo-focus ─ diversity (MMR) ─ cost (Valhalla matrix) ─ optimize
  │                                                       │
  └─ validate ─ render (Valhalla /route) ─ explain ────────┘
```

Files: `backend/agent/planner/{preprocess,intent,resolve,retrieve,rerank,diversity,cost,optimize,validate,render,explain,pipeline}.py`,
clients in `backend/agent/{jev,valhalla_client,search}.py`, tunables in `backend/agent/constants.py`.

## Out of scope

- No `/routes/ready` endpoint, no `ready_routes` table (no source for it).
- No continuous ingest pipeline — `parse_places.py`, `enrich_places.py`, `apply_curated.py`,
  `ingest_osm.py` and `load_osm.py` are one-shot.
- No local ML models: embeddings, intent and rerank are OpenRouter calls (Jev is the
  typed-decision model; there is no local cross-encoder any more).
