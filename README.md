# Grodno walking-route POC

Stack: **Valhalla** (routing engine) + **Postgres/PostGIS/pgvector** (places) +
**FastAPI agent** (free-text RU/EN → pedestrian route) + **Vite webapp** (UI).

## 0. Prereqs (host)

```bash
# macOS
brew install docker docker-compose colima osmium-tool wget uv postgresql@16 jq
# Debian/Ubuntu
sudo apt-get install -y docker.io docker-compose-plugin osmium-tool wget jq postgresql-client
curl -LsSf https://astral.sh/uv/install.sh | sh    # uv
```

For network: outbound HTTPS to `download.geofabrik.de` (Valhalla tiles),
`download.geofabrik.de`/`huggingface.co` (the embedding model, at image build time),
`overpass-api.de` (`seed fetch`), and `openrouter.ai` (the query reading). Without
`OPENROUTER_API_KEY` the planner has no reader and refuses a planning request —
see §1.

## 0a. Bring it up (one command, fresh machine)

```bash
git clone <repo> && cd demo
make up      # build + start db, valhalla, agent, frontend; creates .env if missing
make seed    # load the committed data (idempotent)
```

`make up` runs `docker compose up -d --build --wait`, so all four services come
up together with their healthchecks satisfied. `make seed` reads the committed
CSVs, so a fresh restore needs no Overpass, and re-running changes nothing.

`make` on its own lists every target (up, down, logs, seed, fetch, photos, prune,
admin, quality, test, lint). The equivalent without make:

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose --profile seed run --rm seed        # the one seed command
```

UI: <http://localhost/>. The agent runs **inside compose** (the frontend's nginx
proxies to `agent:8080`). On a host whose Docker bridge filters container→container
traffic, use `docker compose -f docker-compose.yml -f docker-compose.host.yml up -d`
(see §9).

**No `OPENROUTER_API_KEY`?** The planner cannot read a query, so `/routes/generate`,
`/routes/reroute` and `/routes/explain` answer **503 `llm_not_configured`**; `/health`
reports `llm: false` and `status: "degraded"`. The catalogue endpoints (`/places`,
`/routes/itineraries`, `/routes/services`) need no reading and keep working.
Embeddings are local either way.

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
`AGENT_UPSTREAM` / `VALHALLA_UPSTREAM` (`docker-compose.yml`). Both default to the
compose service names (`agent:8080`, `valhalla:8002`). On a host whose Docker bridge
filters container→container traffic, use the `docker-compose.host.yml` override, which
switches them to `host.docker.internal` (see §9).

## 2. Python env (uv)

```bash
cd backend
uv sync                    # runtime + dev (ruff/pyright/pytest) into backend/.venv
.venv/bin/python -c "from api.main import app; print('OK')"
```

Pinned Python is `3.12`. `uv` resolves everything in `backend/pyproject.toml`;
there is no `requirements.txt`.

## 3. Seed the DB

One command — `python -m seed` (the `seed` compose service runs exactly this):

```bash
docker compose --profile seed run --rm seed            # full restore / refresh
docker compose --profile seed run --rm seed fetch      # re-acquire OSM sights/services first
docker compose --profile seed run --rm seed prune      # dry-run: list rows outside the region
docker compose --profile seed run --rm seed --dry-run  # validate + report, offline
```

It reads the committed CSVs (`backend/data/*.csv`), validates and quarantines
out-of-region rows, upserts through **one guarded SQL** keyed on `source_url`,
loads areas/aliases/sources, embeds locally and prints a coverage report. It is
idempotent: a second run inserts nothing new and never overwrites a curated
category. `--report PATH` writes the machine-readable JSON.

From the host venv (no Docker) it is the same command:

```bash
cd backend && .venv/bin/python -m seed --dry-run       # offline: no DB, no network
```

`fetch` writes versioned files (`data/places/places_osm_raw.csv`, `data/places/places_poi.csv`)
and is the **only** step that touches Overpass; `apply` never does. The Overpass
bbox covers a slice of Lithuania and Poland, so every row is checked against
`domain.geofence.inside_project_area` (Grodno ADM1 polygon) before it is written;
`data/geo/belarus_border_keep.json` lists the documented exceptions.

Spot-check:
```bash
docker exec grodno-db psql -U grodno -d grodno -c "
SELECT count(*) AS n, count(embedding) AS with_emb FROM places;"
```

## 5. The agent

The agent is a compose service and comes up with the rest of the stack. To run it
from the checkout instead (e.g. to edit code without an image rebuild):

```bash
cd backend
export DATABASE_URL=postgresql://grodno:grodno@localhost:5432/grodno
export VALHALLA_URL=http://localhost:8002
export OPENROUTER_API_KEY=sk-or-...    # required to plan; see below
.venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8080
```

**Embeddings are local** (`infra/embeddings.py`): `intfloat/multilingual-e5-small`
(384-d) through fastembed/ONNX on the CPU. The weights are baked into the image at
build time, so there is no key to set and no first-call download for retrieval —
the vector signal is always on.

`OPENROUTER_API_KEY` controls **only** the query reading. The tool-using PydanticAI
agent (`planner/agent_interpret.py`) reads the request; without a key there is no
reader, so `planner/intent.py::build_requirements` raises and the planning endpoints
answer 503 rather than guessing with a keyword parse. There is no re-scoring stage —
`retrieve()` already fuses the signals with RRF and that order *is* the relevance order.

## 5a. Accounts, visits and the admin panel (spec 005)

Signed-in accounts sit **beside** the anonymous client of spec 003, not on top of
it: the browser's `X-Client-Id` is *adopted* on register/login, so routes and
preferences saved before signing in stay reachable. Migration
`db/migrations/0008_accounts_visits.sql` (mirrored in `db/init.sql`) adds `users`,
`user_sessions` (only `sha256(token)` is stored) and `visited_places`.

Endpoints — all answer machine reason codes, and the session is an **HttpOnly
cookie** (`grodno_session`), never a JS-readable token:

| Method & path | What |
|---|---|
| `POST /auth/register` `/auth/login` `/auth/logout`, `GET /auth/me` | account + session cookie |
| `GET /me/visited`, `PUT`/`DELETE /me/visited/{place_id}`, `POST /me/visited` | the tourist's «посещённые места» (bulk for a walked route) |
| `GET /admin/users`, `PATCH`/`DELETE /admin/users/{id}` | list users, change role, delete |
| `GET`/`POST /admin/places`, `PATCH`/`DELETE /admin/places/{id}`, `GET /admin/stats` | place management + dashboard |

The first administrator is created by a script — there is **no** «first sign-up
wins admin»:

```bash
make admin EMAIL=boss@example.com                                     # password prompt
# or non-interactive: GRODNO_ADMIN_PASSWORD=... make admin EMAIL=boss@example.com
```

UI: `/login`, `/register`, `/visited`, `/admin` are full pages (not map tabs), with
an account control rendered on every page (`components/account/account-bar.tsx`).
A place card offers «отметить посещённым» to signed-in tourists. `/visited` shows the
marked places **as a list and on a map** (the same shared `PlaceMap` the admin uses:
a row click frames its pin, a pin click highlights its row). The admin «Места»
tab keeps the list and a map side by side **at all times**: the map pins every place
in the list, a row click frames its pin, and a pin click highlights and reveals its
row. In edit mode that pin is draggable — dropping it rewrites `lat`/`lon` and
«сохранить» PATCHes them (blank coordinates are *omitted* from the body, since a
present key would write the NOT NULL column). The webapp's nginx
proxies `/auth/`, `/me/` and `/admin/` to the agent; the *bare* `/admin`, `/login`
and `/visited` stay SPA routes.

**Sign-in is mandatory**: the map (`/`, `/$activeTab`), `/visited` and `/admin` are
gated by a router `beforeLoad` guard (`utils/auth-guard.ts` + `authQueryOptions`) that
redirects an unauthenticated visitor to `/login?redirect=<path>`; only `/login` and
`/register` are public. The gate is client-side; the stateless planner endpoints
(`/routes/*`, `/places`) stay open by design (see spec 005 §5a).

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

## 6a. Quality: is it still good?

One package, `backend/quality/` — routes (geometry), compliance (did the request
survive the pipeline), and evals (which flow stage is to blame). See
`backend/quality/README.md` for the layers and the case schema.

```bash
cd backend
.venv/bin/python -m quality               # geometry: live run against the API
.venv/bin/python -m quality --golden      # compliance: live run
.venv/bin/python -m quality.evals         # the flow stages
.venv/bin/python -m quality.report        # the one-page readout over all three
```

`.venv/bin/python -m quality.report` measures nothing itself: it reads what the runs
wrote under `quality/reports/`. A layer nobody measured prints as
**«НЕ ИЗМЕРЯЛОСЬ»**, never green, and the page exits non-zero when **none** of the
three has numbers — so an empty page cannot pass for a good one.

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
`python -m seed --dry-run` → `pytest -q`). Run them before committing; a red gate
means the tree is not green, so nothing here is «green until someone looks».

Notes that make the gate reproducible:
- `pyright` type-checks `agent` and `seed` (`include` in `pyproject.toml`);
- the `seed --dry-run` CI step validates the committed CSVs offline, so bad data
  fails the build rather than the seed;
- `pyright` is pointed at `.venv` (`venvPath`/`venv` in `pyproject.toml`) — without
  that it cannot resolve `psycopg`/`pydantic` and reports them as missing imports;
- `ruff` deliberately ignores the ambiguous-unicode rules (`RUF001`-`RUF003`): the
  product's own language is Russian, so Cyrillic in strings is not a finding.

## 8. Configuration

Environment carries secrets, deployment addresses and two deliberate escape hatches —
nothing that belongs in review (`core/config.py`, `agent/planner/interpret_cache.py`):

| Variable | What it is |
|---|---|
| `OPENROUTER_API_KEY` | secret; the query reading — **required to plan** (embeddings are local) |
| `DATABASE_URL` | Postgres DSN (agent and seed; default is the local compose one) |
| `VALHALLA_URL` | routing engine address |
| `AGENT_HOST` / `AGENT_PORT` | bind address |
| `AGENT_INTERPRET_MODEL` | optional override of the interpretation model, so a benchmark can pin one |
| `CACHE_BUST=1` | turn the in-process reading/embedding cache off for this process (demos, measurement runs) |
| `INTERPRET_CACHE_SIZE` / `INTERPRET_CACHE_TTL_S` | bounds of that cache (defaults 128 entries / 30 min) |

Everything else is reviewable code in `agent/constants.py` — models, weights and limits:
`RRF_K`, `MMR_LAMBDA`, `RETRIEVAL_POOL_SIZE`,
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
`len(sources) >= 6 AND len(targets) >= 7`. `infra/valhalla_client.py` chunks around it
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
up. They are **not** mounted into the container, so pipe them in:

```bash
docker exec -i grodno-db psql -U grodno -d grodno < backend/db/migrations/0004_places_taxonomy.sql
docker exec -i grodno-db psql -U grodno -d grodno < backend/db/migrations/0009_local_embeddings_384.sql
```

If a fresh volume comes up *without* those tables, `init.sql` has drifted from the
migrations again — fix that, not the seed.

**Agent returns `503 UpstreamUnavailable` on every request.** Valhalla tile build didn't
finish, or the `pgdata` volume lost embeddings — re-run the seed:
`docker compose --profile seed run --rm seed`. Check `docker logs grodno-valhalla`.

**No semantic results (only keyword hits).** The rows have `embedding IS NULL` — e.g. the
`0009` migration ran but the seed was not re-run. `python -m seed` embeds everything and
the spot-check in §3 shows `count(embedding)`. (A missing `OPENROUTER_API_KEY` does not affect retrieval — embeddings are local.)

**`ConnectError` on `localhost:8002`.** Valhalla isn't ready. Poll `/status` until 200.

**Container→container traffic blocked** (agent 502s, `psycopg` timeouts to `db`). Use the
host-network override:
```bash
docker compose -f docker-compose.yml -f docker-compose.host.yml up -d
```

**Overpass 504 on the full-voblast fetch.** `seed fetch` retries. Save one successful
response and replay it with `seed fetch --input-json <file>`.

## 10. Architecture

```
Browser → nginx :80  (frontend container)
  │
  ├─ /routes/*  → proxy → agent :8080
  │                           │
  │               api.main → agent.planner.Pipeline
  │                           │
  │  ┌─ preprocess ─ interpret (PydanticAI over OpenRouter; refuses without a key) ─┐
  │  │                                                                               │
  │  ├─ resolve ─ retrieve (vector + keyword + must-visit, RRF fusion) ─────────────┤
  │  │              vector = LOCAL embeddings (infra/embeddings.py, CPU ONNX)      │
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
allows `X-Client-Id`. `/auth/`, `/me/` and `/admin/` (spec 005) are proxied as well —
the trailing slash is deliberate, so the *bare* `/admin` page stays an SPA route.

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

- **Opening hours.** `seed fetch` stores raw OSM `opening_hours` strings as
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

Opening-hours and prices in the dataset are advisory (`source_url` points to the
verifiable source); they must be confirmed before use.

## Out of scope

- No `/routes/ready` endpoint, no `ready_routes` table.
- No continuous ingest pipeline — `python -m seed fetch` is a deliberate one-shot that
  refreshes the versioned CSVs; `apply` never touches the network.
- The query reading is an OpenRouter call (optional). Embeddings are **local** (a CPU
  ONNX model baked into the image), not an API call.
- `planner/verify.py` (independent post-route verifier against `TripRequirements`) is
  merged and runs on every request: it decides `status`/`requirements` from the final
  route and the Valhalla geometry, never from the interpretation model.
