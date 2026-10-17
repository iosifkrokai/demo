# Grodno walking-route planner

Free-text query → a walkable route over real sights.

Stack: **Valhalla** (routing) + **Postgres/PostGIS/pgvector** (places) +
**FastAPI agent** (query → route) + **Vite webapp** (UI), served by nginx.

## Prereqs (host)

Docker with the compose plugin runs the whole product. The host venv (via `uv`) is
needed only for `make migrate`, `make quality` and `make test-backend`.

```bash
# macOS
brew install docker docker-compose colima osmium-tool wget uv postgresql@16 jq
# Debian/Ubuntu
sudo apt-get install -y docker.io docker-compose-plugin osmium-tool wget jq postgresql-client
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Outbound HTTPS must reach `download.geofabrik.de` (tiles), `huggingface.co` (the
embedding model, baked at image build time), `overpass-api.de` (`db.seed fetch`)
and the configured model endpoint.

## Bring it up

```bash
git clone <repo> && cd demo
make up       # build + start db, valhalla, agent, frontend; creates .env if missing
make migrate  # apply the Alembic schema
make seed     # project the committed data into Postgres (idempotent)
```

`make up` runs `docker compose up -d --build --wait`, so all four services start
once their healthchecks pass. The schema is owned by Alembic, not a container
startup script, so **`make migrate` must run before `make seed`** on a fresh
volume. Both are idempotent. UI: <http://localhost/>.

**No model key?** The agent cannot plan, so `/routes/generate`, `/routes/reroute`
and `/routes/explain` answer **503 `llm_not_configured`**; `/health` reports
`llm: false` and `status: "degraded"`. The catalogue endpoints (`/places`,
`/routes/itineraries`, `/routes/services`) need no model. Embeddings are local.

## Configuration

Secrets, addresses and two deliberate escape hatches live in the environment
(`core/config.py`); models, weights, thresholds and limits are code in
`core/constants.py`. `.env.example` holds the committed defaults.

The endpoint must speak the OpenAI chat format — OpenRouter, a gateway in front of
it, or OpenAI itself. It must also accept a **forced tool call**: the agent hands
in its plan by calling a terminal tool, so PydanticAI sends `tool_choice:
"required"`, and a model that refuses it answers every planning request with a 400.
Gateway thinking modes refuse it; the `-no-thinking` variants do not.

| Variable | What it is |
|---|---|
| `LLM_MODEL` | the model id; defaults to the one in `core/config.py` |
| `OPENROUTER_API_KEY` | OpenRouter or any OpenAI-shaped endpoint — **needed to plan** |
| `OPENROUTER_BASE_URL` | optional; a gateway in front of OpenRouter |
| `DATABASE_URL` | Postgres DSN |
| `VALHALLA_URL` | routing engine address |
| `AGENT_HOST` / `AGENT_PORT` | bind address |
| `LOGFIRE_TOKEN` | optional; without it tracing is a no-op, not an error |
| `CACHE_BUST=1` | turn the in-process reading cache off for this process |

## Architecture

```
Browser → nginx :80 (frontend container)
  │
  ├─ /routes/*, /places, ^/(auth|me|admin)/              → agent :8080
  │                           │
  │               api.main → agent.client.plan_with_agent
  │                           │
  │  reader_brief ─ read (PydanticAI; refuses without a key)
  │  retrieve (vector + keyword + must-visit, RRF fusion)
  │             vector = local embeddings (db/embeddings/model.py, CPU ONNX)
  │  the model calls tools — find_places / plan_walk / verify
  │  plan_walk = cost (Valhalla matrix) ─ optimize ─ validate ─ render (/route)
  │  submit_plan = measure again ─ verify (deterministic) ─ the wire
  │
  └─ /route, /isochrone, /optimized_route, /status, /locate, /height, /tile
          → valhalla :8002
```

Layers: `api → agent → routing → db → core`, and nothing imports upward.
`routing/` imports `core` and `db`, never `agent`. Nothing above `db/store`
writes SQL or holds a connection. `tests/test_layer_boundaries.py` enforces it.

Two request fields change the shape of the answer:

- `result_mode="catalogue"` — a list of matching places grouped by town, with no
  geometry, ordering, budget trim or geo focus. Verification is membership-only.
- `round_trip=true` — the tour closes on the tourist's position (or the first stop)
  and every leg, including the walk out, counts against the budget.

A refinement turn enters with the route as it stands and applies one typed
operation (add / remove / reorder), or refuses with a reason code.

## Data and seeding

**Data is code.** Datasets are versioned text files under `backend/db/data/`; the
database is a projection rebuilt by `python -m db.seed`. Never edit rows by hand.

Layout: `places/` (datasets), `geo/` (boundaries), `photos/` (hints + resolution),
`external/` (the contract for data an outside body sends), plus `taxonomy.csv` and
`itineraries.json`, which are projected into reference tables.

Sight and service rows share a pipe-delimited 14-column shape:

```
name|category|district|town|lat|lon|blurb|fun_fact|fun_facts|opening_hours|ticket_price|visit_minutes|links|source_url
```

`places_curated.csv` uses a 7-column shape and reaches its target by name.
`district` is derived from the polygon the point falls in, never authored.
`opening_hours` and `ticket_price` are reference values; `source_url` is what to
check them against.

A full apply: `city:`/`region:` rows (hand-authored; an invalid row is fatal),
`osm:`/`osm_poi:` rows (invalid or out-of-area rows are quarantined), curated rows
by name last, then areas, aliases, categories, itineraries, embeddings, and a
prune of rows no file carries any more. Upserts use natural keys — `source_url`,
`(provider, external_id)`, category `code`, itinerary `id` — so a re-run inserts
nothing new.

Duplicates are resolved in memory before the write: a hand-authored row shadows
an automatic one within 150 m, and two automatic rows of the same name and
category collapse within 5 m. `place_aliases.json`, `place_merges.json` and
`place_streets.json` name the cases no rule can see.

The seed stamps a `data_version` digest of the committed inputs; the planner
carries it in its measurement cache key, so a re-seed misses instead of replaying.

**Curated categories are protected.** `category_source` says who owns a row
(`curated` / `dataset` / `auto`); the seed's guarded upsert keeps an automatic
writer from overwriting a protected one, and a DB trigger reverts a category
change unless the transaction opted in.

Known gaps: opening hours and ticket prices are too sparse for an "open now" or
price filter; English names cover only objects OSM tagged `name:en`; same-name
pairs within the radius are merge candidates, not proof.

## Quality

Six layers, six questions:

| Layer | Question | Cases |
|---|---|---|
| routes | is our walk close to a reference walk? | `cases/routes/*.json` |
| curated | is our walk close to the walk we sell? | `cases/curated/*.json` |
| compliance | does the plan meet the request's conditions? | `cases/compliance/*.json` |
| trajectory | which tools did the agent call, with what arguments? | `cases/trajectory.jsonl` |
| evals | which stage is to blame when the answer is bad? | `cases/*.jsonl` |
| retrieval | does the pool reach the stops we know are in the data? | the routes walks |

```bash
cd backend
.venv/bin/python -m quality               # geometry, live (needs the stack)
.venv/bin/python -m quality --replay DIR  # recompute from a snapshot (offline)
.venv/bin/python -m quality --golden      # compliance, live
.venv/bin/python -m quality --curated     # ready-made walks, live
.venv/bin/python -m quality.trajectory    # scripted tool trajectory
.venv/bin/python -m quality.evals         # flow stages (some offline)
.venv/bin/python -m quality.report        # the one-page readout
```

Nothing generated is committed. `quality.report` prints **«НЕ ИЗМЕРЯЛОСЬ»** for a
layer with no artifact — it never shows an unmeasured layer as green, and exits
non-zero when no layer has numbers.

Compliance failures are machine-readable: `api_error`, `wrong_status`,
`too_few_places`, `missing_mandatory_category`, `forbidden_category_present`,
`missing_named_place`, `requirement_unmet`, `requirement_status`,
`moment_not_applied`, `out_of_region_point`, `over_budget`, `result_mode_mismatch`,
`ru_en_parity_mismatch`.

No layer measures human taste, the mobile card layout, tile translation, regions
other than Grodno, or the real detour minutes to reach a point beside the line.

## Tests

Three kinds, and only three:

1. **Endpoint success coverage** (`tests/test_endpoints.py`) — one success case per
   HTTP endpoint, against the live stack at `GRODNO_BASE_URL` (default
   `http://localhost:8080`). Tests skip when the stack is unreachable; planning
   endpoints skip when `llm=false`; admin endpoints skip without
   `GRODNO_ADMIN_EMAIL`/`GRODNO_ADMIN_PASSWORD`. A skip is not a pass.
2. **Pure leaf math** (`tests/test_routing_math.py`) — `routing/` with no DB,
   network or model: time sentinels, snap radii, failure markers.
3. **Offline trajectory** (`tests/test_agent_trajectory.py`) — a `FunctionModel`
   replays the whole agent path with no API key.

`tests/test_layer_boundaries.py` holds the architecture: leaves never reach
upward, logic layers speak no SQL, `db/models/**` imports the standard library
only.

## The admin panel

`/admin` lists users and places and shows the counts. The first administrator is
made with `make admin EMAIL=boss@example.com` — the account is created, or an
existing one is promoted; its password is left alone either way.

## Smoke test

```bash
curl -s localhost:8080/health | jq
curl -s localhost:8080/places | jq '.items | length'
curl -sX POST localhost:8080/routes/generate -H 'content-type: application/json' \
  -d '{"query":"Хочу погулять по замкам Гродно","time_budget_minutes":120}' | jq '.points[].name'
```

## Troubleshooting

**"relation 'places' does not exist", or the seed failing on a fresh volume.**
The schema is owned by Alembic; apply it with `make migrate` before `make seed`.

**`503 (valhalla ... sources_to_targets failed after retries)`.** The image answers
500 `Could not find candidate edge used for label` for some matrix shapes with
`len(sources) >= 6 AND len(targets) >= 7`. `routing/valhalla/matrix.py` chunks
around it (`MATRIX_MAX_SOURCES` / `MATRIX_MAX_TARGETS`).

**Route has no polyline / `length_km: null`.** `/route` answers 500 `Could not
find candidate edge used for destination label` for some POI coordinates. Fixed by
sending `radius: 100` per location (`LOCATION_SNAP_RADIUS_M`).

**No semantic results (only keyword hits).** The rows have `embedding IS NULL`.
Re-run `python -m db.seed`. A vector describes the text it was built from (name,
blurb, category, town); changing that recipe requires `python -m db.seed reembed`
— a plain seed fills `NULL`s and nothing else.

## What is not verified / not promised

- **Opening hours.** Raw OSM strings, harvested as-is; may be stale or absent.
- **Ticket prices.** OSM `charge`/`fee` tags, unverified against the venue.
- **Step-free / wheelchair access.** Default pedestrian profile; no kerb, ramp or
  lift audit.
- **Bus schedules and ticket purchase.** No transit data is ingested.
- **Coordinates are reference points.** A POI coordinate may not be the accessible
  entrance.
