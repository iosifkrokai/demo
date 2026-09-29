# Grodno walking-route POC

Stack: **Valhalla** (routing engine) + **Postgres/PostGIS/pgvector** (places) +
**FastAPI agent** (free-text RU/EN → pedestrian route) + **Vite webapp** (UI).

**Plan of record:** `docs/specs/002-grodno-guide-rebuild/` (spec, plan, tasks) and
`docs/specs/constitution.md`. Six workstreams are currently in progress there; this
README describes what is shipped and running today.

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
optional** — without it the pipeline degrades to keyword-only retrieval (no
embeddings, and the request is read by the deterministic parser); routes are still
built.

## 0a. One command (fresh machine)

```bash
git clone <repo> && cd demo
bash scripts/bootstrap.sh --check   # verify prerequisites, change nothing
bash scripts/bootstrap.sh           # .env + compose + uv sync + all seeds
```

`bootstrap.sh` is idempotent (every seed upserts on `source_url`), skips the OSM
ingest with `--skip-osm`, and prints the agent command at the end. Without
`OPENROUTER_API_KEY` it seeds without embeddings and warns (keyword-only
retrieval until you re-run the seeds with the key).

## 1. Web-app subdir

The webapp lives in `frontend/`. It shares the **single root `.env`** (Vite reads it
via `envDir: '..'`); there is no separate `frontend/.env`. Copy the committed example
at the repository root before building:

```bash
cp .env.example .env
```

`.env` is gitignored, so a fresh clone has none; the root `.env.example` holds the
defaults — both URLs empty, which is what makes the app work behind a forwarded port
(Codespaces, tunnels): the UI talks to its own origin and nginx proxies `/routes/*` →
agent, `/route`, `/status`, `/isochrone`, … → Valhalla.

`VITE_*` vars are baked at image build time, so this file MUST exist before
`docker compose build`. `frontend/.dockerignore` must NOT exclude `package-lock.json` —
the Dockerfile runs `npm ci`, which fails without the lockfile in the build context.

Upstreams are rendered into `frontend/nginx.conf` at container start from
`AGENT_UPSTREAM` / `VALHALLA_UPSTREAM` (`docker-compose.yml`). The default is
`host.docker.internal:…` — the Docker host on Linux (via the `host-gateway` mapping in
compose), macOS and Windows — because the agent runs there and some hosts block
container→container traffic on the compose bridge. Where that traffic is allowed,
override with `AGENT_UPSTREAM=agent:8080 VALHALLA_UPSTREAM=valhalla:8002`.

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

The agent **is** defined as a compose service (`agent`, wired to `db:5432` /
`valhalla:8002`) for a hands-off deployment, but the documented flow runs it from the
checkout via `uvicorn` (step 5) on purpose: code edits then need no image rebuild.
Use `docker compose up -d agent` when you want the container instead — it reads
`OPENROUTER_API_KEY` from the environment.

## 4. Seed the DB

```bash
cd backend
export DATABASE_URL=postgresql://grodno:grodno@localhost:5432/grodno
export OPENROUTER_API_KEY=sk-or-...        # required for embeddings; omit for keyword-only

# 4a. Curated places (city + voblast CSV) + embeddings for every row.
#     Idempotent: upsert keyed on source_url.
.venv/bin/python scripts/seed_region.py            # dry: prints the plan, writes nothing
.venv/bin/python scripts/seed_region.py --embed

# 4b. Optional: widen coverage with OSM POIs (~4.4k rows for the voblast)
.venv/bin/python scripts/ingest_osm.py             # Overpass → data/places_osm_raw.csv (~3 min)
.venv/bin/python scripts/load_osm.py --dry-run     # validate + print the plan
.venv/bin/python scripts/load_osm.py               # upsert + embed
```

```bash
# 4c. Everyday POIs: cafes, restaurants, toilets, hotels.
#     These are placed AROUND the current route (500 m of its stops),
#     not across the whole region (see CONVENIENCE_RADIUS_M in agent/constants.py).
.venv/bin/python scripts/ingest_poi.py                        # Overpass → upsert + embed
.venv/bin/python scripts/ingest_poi.py --dry-run --limit 5    # inspect, write nothing
```

`ingest_osm.py` also takes `--input-json <saved Overpass response>` (skip the network),
`--district-mode nominatim` (real reverse geocoding, 1 req/s) and `--limit N`.
`load_osm.py` takes `--dry-run`, `--limit`, `--no-embed`, `--path`.
`ingest_poi.py` takes `--dry-run`, `--limit`, `--no-embed`, `--bbox`, `--input-json`.

The Overpass bbox covers a slice of Lithuania and Poland, so every row is checked against
`agent.geofence.inside_project_area` (Grodno ADM1 polygon, `data/grodno_border.json`)
before it is written. A DB filled before the filter existed is cleaned with:

```bash
export DATABASE_URL=postgresql://grodno:grodno@localhost:5432/grodno
.venv/bin/python scripts/purge_foreign_places.py           # dry run
.venv/bin/python scripts/purge_foreign_places.py --apply
```

A handful of POIs sit just outside the ADM1 polygon but are verifiably inside the
region; they are listed in `data/belarus_border_keep.json` (each verified with
Nominatim) and treated as exceptions by `inside_project_area`, so ingest and purge
agree on the same set.

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
export DATABASE_URL=postgresql://grodno:grodno@localhost:5432/grodno
export VALHALLA_URL=http://localhost:8002
export OPENROUTER_API_KEY=sk-or-...    # optional; without it the agent is keyword-only

.venv/bin/python -m uvicorn agent.main:app --host 0.0.0.0 --port 8080
```

Without `OPENROUTER_API_KEY`: embeddings are skipped and the request is read by the
deterministic parser (`planner/intent.py::build_requirements` → the regex/keyword
reading, over the same CATEGORY_SYNONYMS map retrieval uses). Routes are still built;
`/health` reports `"llm": false`.

With key: embeddings via OpenRouter (`openai/text-embedding-3-small`, 1536 dims) and
the reading by the tool-using PydanticAI agent (`planner/agent_interpret.py`) over
OpenRouter. There is no re-scoring stage — `retrieve()` already fuses the signals with
RRF and that order *is* the relevance order. No local models, no first-call download.

## 6. Smoke tests

```bash
# Health
curl -fsS localhost:8080/health | jq

# Generate a route (Russian query)
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

`backend/benchmarks/routes/*.json` holds reference walks. The runner drives the live
HTTP API:

```bash
cd backend
.venv/bin/python scripts/bench_routes.py --base-url http://localhost:8080
# prints a table and writes backend/benchmarks/report.json + report.md
```

Two layers measure different things, and the runner can produce both:

```bash
# Golden set: did the request survive the pipeline (mandatory categories, prohibitions,
# region, budget, RU/EN parity)? Writes benchmarks/compliance.json + compliance.md.
.venv/bin/python scripts/bench_routes.py --golden --snapshot "" --report-dir benchmarks

# One page that reads the three layers (geometry / golden / evals) and shows the
# freshness of each; a layer nobody measured prints as «НЕ ИЗМЕРЯЛОСЬ», never green.
.venv/bin/python reports/quality.py
```

`reports/quality.py` measures nothing itself: it reads what the layers wrote. It
exits non-zero when **none** of the three has numbers, so an empty page cannot pass
for a good one.

## 7. Dev workflow

```bash
cd backend
uv sync                       # create/refresh .venv from the locked deps

# Lint (auto-fix safe issues)
.venv/bin/ruff check --fix .

# Format
.venv/bin/ruff format .

# Type-check (strict-ish, ~10-20 sec on a cold pyright cache)
.venv/bin/pyright

# Tests
.venv/bin/python -m pytest -q
```

These three checks are the gate, and they run in CI on every push and PR
(`.github/workflows/ci.yml`: `uv sync --frozen` → `ruff check .` → `pyright` →
`pytest -q`). Run them before committing; a red gate means the tree is not green, so
nothing here is «green until someone looks».

Notes that make the gate reproducible:
- the dev dependency group carries `requests`/`beautifulsoup4` because the suite
  imports the one-shot scripts (`tests/test_seed_geofence.py` → `scripts/parse_places.py`);
  without them a plain `uv sync` cannot even collect the suite;
- `pyright` is pointed at `.venv` (`venvPath`/`venv` in `pyproject.toml`) — without
  that it cannot resolve `psycopg`/`pydantic` and reports them as missing imports;
- `ruff` deliberately ignores the ambiguous-unicode rules (`RUF001`-`RUF003`): the
  product's own language is Russian, so Cyrillic in strings is not a finding.

## 8. Configuration

Environment carries secrets, deployment addresses and two deliberate escape hatches —
nothing that belongs in review (`agent/config.py`, `agent/planner/interpret_cache.py`):

| Variable | What it is |
|---|---|
| `OPENROUTER_API_KEY` | secret; embeddings + the interpretation agent (also read by the seed scripts) |
| `DATABASE_URL` | Postgres DSN (agent and seed scripts; default is the local compose one) |
| `VALHALLA_URL` | routing engine address |
| `AGENT_HOST` / `AGENT_PORT` | bind address |
| `AGENT_INTERPRET_MODEL` | optional override of the interpretation model, so a benchmark can pin one |
| `CACHE_BUST=1` | turn the in-process reading/embedding cache off for this process (demos, measurement runs) |
| `INTERPRET_CACHE_SIZE` / `INTERPRET_CACHE_TTL_S` | bounds of that cache (defaults 128 entries / 30 min) |

Everything else is reviewable code in `agent/constants.py` — models, weights and limits:
`EMBED_MODEL`, `RRF_K`, `MMR_LAMBDA`, `RETRIEVAL_POOL_SIZE`,
`MMR_POOL_SIZE`, `GEO_FOCUS_KM`, `GEO_FOCUS_DISCOVERY_MAX_KM`,
`MAX_WALK_LEG_KM` / `WALK_LEG_BUDGET_SHARE` (walkability),
`NAME_MATCH_MIN_SIM` (named-place → must-visit threshold),
`DUPLICATE_RADIUS_M` / `DUPLICATE_NAME_RADIUS_M` (same-POI deduplication),
`CONVENIENCE_RADIUS_M` / `CONVENIENCE_MAX_ADDED`, `VALHALLA_MAX_LOCATIONS`,
budget bounds, and the category → visit-minutes table. Changing one is a code-review
decision: edit → tests → commit.

A named-place token becomes a **must-visit** only when it matches a POI *name* above
`NAME_MATCH_MIN_SIM`; a token that only matches a town/district becomes an **area anchor**
for the geo focus instead, so «замки Гродно» is not pinned to one arbitrary Grodno row.

## 9. Troubleshooting

**`503 (valhalla ... sources_to_targets failed after retries)`.** Valhalla 3.5.1 answers
500 `Could not find candidate edge used for label` for matrix shapes with
`len(sources) >= 6 AND len(targets) >= 7`. `agent/valhalla_client.py` chunks around it
(`MATRIX_MAX_SOURCES` / `MATRIX_MAX_TARGETS`) with a per-pair `/route` fallback.

**Route has no polyline / `length_km: null`.** `/route` answers 500
`Could not find candidate edge used for destination label` for some POI coordinates.
Fixed by sending `radius: 100` per location (`LOCATION_SNAP_RADIUS_M` in
`valhalla_client.py`); `search_radius` / `street_side_tolerance` do NOT help.

**"relation 'places' does not exist", or the seed failing on a fresh volume.**
`db/init.sql` loads only on the FIRST start of the `db` container, and it carries the
COMPLETE current schema: `places`, `place_aliases`, `place_sources`, `areas`, `clients`,
`client_preferences`, `saved_routes`, the `places.category_source` column and the
curated-category guard trigger (the same DDL as `db/migrations/0004`/`0005`). Those
migration files stay as the idempotent path for volumes created before `init.sql` caught
up:

```bash
docker exec grodno-db psql -U grodno -d grodno -f db/migrations/0004_places_taxonomy.sql
```

If a fresh volume comes up *without* those tables, `init.sql` has drifted from the
migrations again — fix that, not the seed.

**Agent returns `503 UpstreamUnavailable` on every request.** Valhalla tile build didn't
finish or the `pgdata` volume lost embeddings — re-run `scripts/seed_region.py --embed`.
Check `docker logs grodno-valhalla`.

**Keyword-only results (no semantic search).** `OPENROUTER_API_KEY` is missing or the
rows have `embedding IS NULL` — see the spot-check in section 4. `/health` reports
`"llm": false` in this state.

**`ConnectError` on `localhost:8002`.** Valhalla isn't ready. Poll `/status` until 200.

**Overpass 504 on the full-voblast ingest.** `ingest_osm.py` retries. Save one successful
response and iterate with `--input-json <file>`.

## 10. Architecture

```
Browser → nginx :80  (frontend container)
  │
  ├─ /routes/*  → proxy → agent :8080
  │                           │
  │               agent.main → agent.planner.Pipeline
  │                           │
  │  ┌─ preprocess ─ interpret (PydanticAI over OpenRouter; deterministic fallback) ─┐
  │  │                                                                               │
  │  ├─ resolve ─ retrieve (vector + keyword + must-visit, RRF fusion) ─────────────┤
  │  │                                                                               │
  │  ├─ geo-focus ─ diversity (MMR) ─ cost (Valhalla matrix) ─ optimize ────────────┤
  │  │                                                                               │
  │  └─ validate ─ render (Valhalla /route) ─ explain ─ verify (deterministic) ──────┘
  │
  │  A refinement turn («добавь кофейню и туалет») enters with the route as it stands and
  │  applies ONE typed operation to it (add / remove / reorder), or refuses with a machine
  │  reason code — it never silently re-plans from scratch.
  │
  │  result_mode=catalogue leaves the same path before geo-focus and answers with the
  │  matching places grouped by town: no geometry, no ordering, no budget trim.
  │
  └─ /route, /status, /isochrone, /locate, /height, /tile
          → proxy → valhalla :8002
```

nginx rule: `location /routes/` proxies the whole prefix to the agent; individual
Valhalla paths are matched by the regex `^/(route|isochrone|optimized_route|status|locate|height|tile)$`
(see `frontend/nginx.conf`). `/clients/` is proxied to the agent too, and its preflight
allows `X-Client-Id`.

Two request fields change the shape of the answer, and both are honoured by the pipeline
rather than merely accepted:

- `result_mode="catalogue"` — the answer is a **list** of the matching places grouped by
  town, with no geometry, no ordering, no budget trim and no geo focus: confining a
  catalogue to one walkable cluster is exactly what made «все костёлы области»
  unanswerable. Verification is membership-only (`verify_catalogue`), so the requirement
  chips carry `*_in_catalogue` reason codes — nothing may claim «на маршруте» when no
  route exists.
- `round_trip=true` — the tour closes back on its own start: `render()` asks Valhalla for
  the return leg and `validate()` counts it against the budget, so a closed tour cannot
  look cheaper than an open one.

`verify` is the honest half of the response: it is deterministic, it re-reads the final
route and the geometry, and it — not the interpretation model — decides `status`
(`ready` / `infeasible` / `degraded` / `pending`) and each requirement's verdict. The
same verdicts reach the client as `interpretation.requirements` plus the explicit
`interpretation.unmet` list.

Planner files: `backend/agent/planner/{preprocess,intent,resolve,retrieve,diversity,cost,optimize,validate,render,explain,refine,verify,interpret_cache,agent_interpret,pipeline}.py`,
clients in `backend/agent/{valhalla_client,search}.py`, tunables in `backend/agent/constants.py`.

## 11. What is not verified / not promised

The following are **not guaranteed** by this system and should not be shown to users as
confirmed facts:

- **Opening hours.** `ingest_poi.py` stores raw OSM `opening_hours` strings as
  harvested; they are not verified against live data and may be stale or absent.
- **Ticket prices and admission fees.** OSM `charge`/`fee` tags are stored as-is,
  without verification against the venue's current policy.
- **Step-free / wheelchair access.** The pedestrian route uses Valhalla's default
  pedestrian profile; there is no audit of kerbs, ramps or lift availability in the
  road graph or the POI dataset.
- **Bus schedules and ticket purchase.** No transit data (GTFS or otherwise) is
  ingested; there is no "buy a ticket" feature and no live schedule information.
- **Coordinates are reference points.** A POI coordinate is the stored geocoded
  position; it may not correspond to the accessible entrance.

See `docs/specs/constitution.md` §7: opening-hours and prices in the dataset are
advisory (`source_url` points to the verifiable source); they must be confirmed before
use.

## Out of scope

- No `/routes/ready` endpoint, no `ready_routes` table.
- No continuous ingest pipeline — `ingest_osm.py`, `load_osm.py` and `ingest_poi.py`
  are one-shot CLI scripts; `parse_places.py`, `enrich_places.py`, `apply_curated.py`
  predate the current structure and are not part of the active seed path.
- No local ML models: embeddings and the query reading are OpenRouter calls.
- `planner/verify.py` (independent post-route verifier against `TripRequirements`) is
  merged and runs on every request: it decides `status`/`requirements` from the final
  route and the Valhalla geometry, never from the interpretation model.
