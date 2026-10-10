# Grodno walking-route POC

Stack: **Valhalla** (routing engine) + **Postgres/PostGIS/pgvector** (places) +
**FastAPI agent** (free-text RU/EN → pedestrian route) + **Vite webapp** (UI).

## Prereqs (host)

Docker (with the compose plugin) runs the whole product; the host venv (via `uv`)
is needed only for the targets that run Python on the host — `make migrate`,
`make quality`, `make test-backend`.

```bash
# macOS
brew install docker docker-compose colima osmium-tool wget uv postgresql@16 jq
# Debian/Ubuntu
sudo apt-get install -y docker.io docker-compose-plugin osmium-tool wget jq postgresql-client
curl -LsSf https://astral.sh/uv/install.sh | sh    # uv, for the host venv
```

Outbound HTTPS reaches `download.geofabrik.de` (tiles), `huggingface.co` (the
embedding model, baked at image build time), `overpass-api.de` (`db.seed fetch`) and
`openrouter.ai` (the reading).

## Bring it up

```bash
git clone <repo> && cd demo
make up      # build + start db, valhalla, agent, frontend; copies .env.example → .env if missing
make migrate # apply the Alembic schema (backend/db/alembic/, upgrade head)
make seed    # load the committed CSVs (idempotent; no network, no Overpass)
```

`make up` runs `docker compose up -d --build --wait`, so the four services start
together once their healthchecks pass. The schema is owned by Alembic, not a
container startup script, so **`make migrate` must run before `make seed`** on a
fresh volume. Both are idempotent: Alembic records the revision in
`alembic_version` (a volume at `head` is a no-op; the DSN comes from `DATABASE_URL`).

`make` alone lists every target. Without make:

```bash
cp .env.example .env
docker compose up -d --build --wait
cd backend && .venv/bin/python -m alembic upgrade head
docker compose --profile seed run --rm seed
```

UI: <http://localhost/>. The agent runs inside compose; the frontend's nginx
proxies to `agent:8080`.

**No `OPENROUTER_API_KEY`?** The planner has no reader, so `/routes/generate`,
`/routes/reroute` and `/routes/explain` answer **503 `llm_not_configured`**;
`/health` reports `llm: false` and `status: "degraded"`. The catalogue endpoints
(`/places`, `/routes/itineraries`, `/routes/services`) need no reading and keep
working. Embeddings are local either way.

## Configuration

Secrets, addresses and two deliberate escape hatches live in the environment
(`core/config.py`); models, weights, thresholds and limits are reviewable code in
`core/constants.py`. `.env.example` holds the committed defaults (frontend build
values, Postgres, proxy upstreams); the variables below are what the agent and
seed read.

| Variable | What it is |
|---|---|
| `OPENROUTER_API_KEY` | secret; the query reading — **required to plan** (embeddings are local) |
| `DATABASE_URL` | Postgres DSN (agent and seed; default is the local compose one) |
| `VALHALLA_URL` | routing engine address |
| `AGENT_HOST` / `AGENT_PORT` | bind address |
| `AGENT_INTERPRET_MODEL` | optional override of the interpretation model (its default is named in `agent/model.py`) |
| `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | optional self-hosted tracing (`telemetry/trace.py`); **both keys** are required, and without them tracing is a no-op |
| `CACHE_BUST=1` | turn the in-process reading/embedding cache off for this process |
| `INTERPRET_CACHE_SIZE` / `INTERPRET_CACHE_TTL_S` | bounds of that cache (defaults 128 entries / 30 min) |

Changing a value in `core/constants.py` is a code-review decision: edit → tests
→ commit.

## Architecture

```
Browser → nginx :80 (frontend container)
  │
  ├─ /routes/*, /clients/, /places, ^/(auth|me|admin)/   → proxy → agent :8080
  │                           │
  │               api.main → planner.Pipeline
  │                           │
  │  preprocess ─ interpret (PydanticAI over OpenRouter; refuses without a key)
  │  resolve ─ retrieve (vector + keyword + must-visit, RRF fusion)
  │             vector = LOCAL embeddings (ml/embeddings.py, CPU ONNX)
  │  geo-focus ─ diversity (MMR) ─ cost (Valhalla matrix) ─ optimize
  │  validate ─ render (Valhalla /route) ─ explain ─ verify (deterministic)
  │
  └─ /route, /isochrone, /optimized_route, /status, /locate, /height, /tile
          → proxy → valhalla :8002
```

nginx (`frontend/nginx.conf`) proxies the agent prefixes above and the Valhalla
paths; the trailing slash on `/auth|/me|/admin/` is deliberate, so the *bare*
`/admin`, `/login` and `/visited` stay SPA routes.

`backend/planner/` is the pipeline itself — preprocess → interpret → resolve →
retrieve → geo-focus → diversity → cost → optimize → validate → render → explain →
verify — with `catalogue` and `refine` as alternate branches. The LLM layer is
`backend/agent/` (client / model / runner / tools / prompts); the Valhalla client is
`backend/planner/valhalla_client.py`, the place queries `backend/db/store/places.py`.

Two request fields change the shape of the answer:

- `result_mode="catalogue"` — a **list** of matching places grouped by town, with
  no geometry, no ordering, no budget trim and no geo focus. Verification is
  membership-only (`verify_catalogue`), so a requirement chip can never claim
  «на маршруте» when no route exists.
- `round_trip=true` — the tour closes on its own start; `render()` asks Valhalla
  for the return leg and `validate()` counts it against the budget.

`verify` is the honest half of the response: deterministic, it re-reads the final
route and geometry and — not the interpretation model — decides `status` and each
requirement's verdict. A refinement turn enters with the route as it stands and
applies one typed operation (add / remove / reorder), or refuses with a reason code.

## Accounts, visits and the admin panel

Signed-in accounts sit **beside** the anonymous `X-Client-Id` client, which is
adopted on register/login. The Alembic baseline creates `users`, `user_sessions`
(only `sha256(token)` stored) and `visited_places`; the session is an **HttpOnly
cookie** (`grodno_session`). `/login`, `/register`, `/visited` and `/admin` are
full pages behind a client-side guard; the first admin is created with
`make admin EMAIL=boss@example.com`.

## Quality

`backend/quality/` holds the three "is it still good?" layers — routes (geometry),
compliance (did the request survive the pipeline) and evals (which stage is to
blame) — plus the one-page readout `python -m quality.report`. See
`backend/quality/README.md`.

## Smoke test

```bash
curl -sX POST localhost:8080/routes/generate -H 'content-type: application/json' \
  -d '{"query":"Хочу погулять по замкам Гродно","time_budget_minutes":120}' | jq '.points[].name'
```

## Troubleshooting

**"relation 'places' does not exist", or the seed failing on a fresh volume.** The
schema is owned by Alembic, not a container startup script: `backend/db/alembic/`
holds one baseline revision creating the complete schema (places, aliases, sources,
areas, clients, saved routes, users/sessions/visited, the curated-category guard).
Apply it with `make migrate`.

**`503 (valhalla ... sources_to_targets failed after retries)`.** Valhalla 3.5.1
answers 500 `Could not find candidate edge used for label` for matrix shapes with
`len(sources) >= 6 AND len(targets) >= 7`. `planner/valhalla_client.py` chunks around
it (`MATRIX_MAX_SOURCES` / `MATRIX_MAX_TARGETS`).

**Route has no polyline / `length_km: null`.** `/route` answers 500 `Could not
find candidate edge used for destination label` for some POI coordinates. Fixed by
sending `radius: 100` per location (`LOCATION_SNAP_RADIUS_M` in
`valhalla_client.py`).

**No semantic results (only keyword hits).** The rows have `embedding IS NULL`.
`python -m db.seed` embeds everything locally — re-run it. (A missing
`OPENROUTER_API_KEY` does not affect retrieval.)

## What is not verified / not promised

The following are **not guaranteed** and must not be shown to users as confirmed
facts:

- **Opening hours.** `db.seed fetch` stores raw OSM `opening_hours` strings as
  harvested; they are not verified against live data and may be stale or absent.
- **Ticket prices and admission fees.** OSM `charge`/`fee` tags are stored as-is,
  without verification against the venue's current policy.
- **Step-free / wheelchair access.** The pedestrian route uses Valhalla's default
  profile; there is no audit of kerbs, ramps or lift availability.
- **Bus schedules and ticket purchase.** No transit data is ingested, and there is
  no live schedule information.
- **Coordinates are reference points.** A POI coordinate is the stored geocoded
  position; it may not correspond to the accessible entrance.