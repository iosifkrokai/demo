# Grodno walking-route POC

Minimal POC: stand up Valhalla (routing engine + demo web-app) for the Grodno region,
fill a Postgres/PostGIS/pgvector DB with sights scraped from planetabelarus.by, and serve a
FastAPI agent that turns free-text Russian queries into pedestrian routes via the same Valhalla.

The agent uses a **local GGUF model via llama-cpp-python** for query parsing — no API keys
needed for the runtime. The parser + enrich steps are one-shot scripts.

## One-time setup

1. Install host prerequisites: `docker`, `docker compose`, `python3.11+`, `osmium-tool`,
   `wget`. Build tools (`gcc`, `cmake`) recommended for llama-cpp-python fallbacks.
   Python deps: `python3 -m venv .venv && . .venv/bin/activate && pip install -r agent/requirements.txt`.
2. Clone the Valhalla demo app as `web-app/` and edit its `.env` (one-time):
   ```bash
   git clone --depth 1 https://github.com/valhalla/web-app.git web-app
   ```
   Then replace `web-app/.env` with:
   ```
   SKIP_PREFLIGHT_CHECK=true
   VITE_VALHALLA_URL=http://localhost
   VITE_NOMINATIM_URL=https://nominatim.openstreetmap.org
   VITE_TILE_SERVER_URL="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
   VITE_CENTER_COORDS="53.6772,23.8232"
   VITE_DEFAULT_COSTING_MODEL=pedestrian
   VITE_CLIENT_ID=grodno-poc
   VITE_AGENT_URL=http://localhost:8080
   ```
   `VITE_*` vars are baked at image build time, so this file must exist before `docker compose build`.
   The webapp ships with a custom `Sidebar` component (in `src/components/sidebar.tsx`) that
   replaces the upstream `RoutePlanner` — it has a prompt at top, editable waypoints in the
   middle, and a Nominatim-based manual-add field at the bottom. These files live only in
   the working tree (the webapp clone has its own .git inside); they're not tracked in this
   repo, so a fresh clone of the upstream won't have them.

## Bring up the stack

```bash
# 1. download Belarus PBF and cut to Grodno Oblast bbox
./scripts/extract_grodno_pbf.sh

# 2. bring up db + valhalla (builds tiles on first start, ~5–10 min)
docker compose up -d db valhalla

# 3. wait for /status 200 (poll)
until curl -fsS http://localhost:8002/status >/dev/null; do sleep 5; done

# 4. build & start the web-app
docker compose up -d --build valhalla-app
```

Open `http://localhost/` in a browser. The left sidebar is the only UI for the POC:
prompt at top, waypoint list in the middle (drag the `⠿` handle to reorder, or use
`↑` / `↓`, or `✕` to delete), Nominatim-based manual-add at the bottom.

## Fill the DB

```bash
. .venv/bin/activate
python scripts/parse_places.py        # ~76 Grodno sights via server-side filter, 4 workers, 0.5s delay, ~20s
export OPENAI_API_KEY=sk-...          # optional — without it, categories stay NULL
python scripts/enrich_places.py       # embeddings always; categories only if OPENAI_API_KEY is set
python scripts/apply_curated.py --dry-run   # preview what the curated CSV would change
python scripts/apply_curated.py             # apply name/category/blurb/fun_fact (must run last)
```

`data/places_curated.csv` is the hand-curated source of truth and must be applied **after**
the scraper and the enrich pass, otherwise scraped names win. See
[`data/data_quality.md`](data/data_quality.md) for the rationale and for the review rule that
applies to `fun_fact`.

> `db/init.sql` only runs when the `pgdata` volume is created. If you are upgrading an
> existing DB, add the newer columns by hand first:
> `docker exec grodno-db psql -U grodno -d grodno -c "ALTER TABLE places ADD COLUMN IF NOT EXISTS fun_fact TEXT;"`

Spot-check:
```bash
psql "$DATABASE_URL" -c "SELECT count(*) AS n, count(embedding) AS with_emb, count(category) AS with_cat, count(fun_fact) AS with_fact FROM places;"
psql "$DATABASE_URL" -c "SELECT name, lat, lon FROM places ORDER BY id LIMIT 10;"
```

## Run the agent

```bash
export DATABASE_URL=postgresql://grodno:grodno@localhost:5432/grodno
export VALHALLA_URL=http://localhost:8002
uvicorn agent.main:app --host 0.0.0.0 --port 8080
```

On first start the agent will print:
- `loading sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 via fastembed (one-time)...`
- `loading local LLM Qwen/Qwen2.5-1.5B-Instruct-GGUF / *q4_k_m.gguf (ctx=2048, threads=4)...`

The first run downloads ~470 MB (embedding) + ~1 GB (LLM) into `~/.cache/`. Subsequent runs
are instant. Override model/file/threads via env: `LLM_MODEL`, `LLM_FILE`, `LLM_CTX`, `LLM_THREADS`.

Smoke test:
```bash
curl -sX POST localhost:8080/routes/generate \
  -H 'content-type: application/json' \
  -d '{"query":"Хочу погулять по замкам Гродно","time_budget_minutes":120}' | jq '.points[] | {name, blurb, fun_fact}'

curl -sX POST localhost:8080/routes/reroute \
  -H 'content-type: application/json' \
  -d '{"point_ids":[1,2,3,4]}' | jq .shape | head -c 200
```

Each point in a response carries `blurb`, `fun_fact` and `visit_minutes` straight from
`places`. The webapp stores them in `directions-store.placeDetails` (keyed by `places.id`) and
renders them next to the map markers: a permanent name+blurb caption under the marker, plus a
card with the fun fact on click (`src/components/map/parts/place-marker-label.tsx`,
`place-card-popup.tsx`).

## Notes / troubleshooting

- **llama-cpp-python install.** Prebuilt wheels exist for Linux x86_64 / macOS arm64 / Win. If
  `pip install` fails to find a wheel for your Python version, force a source build with:
  `CMAKE_ARGS="-DGGML_NATIVE=off" pip install llama-cpp-python --no-binary :all:` (needs gcc + cmake).
  Worst case the agent still starts — `parse_query()` falls back to a regex keyword parser when
  llama-cpp-python is missing or model load fails.
- **First LLM call is slow.** Qwen 1.5B q4 on CPU takes ~1–3 s for one short chat completion
  (especially the first call, before kernel caches warm up). Acceptable for a POC.
- **Categories column.** Without `OPENAI_API_KEY` set, `places.category` stays NULL. The agent
  still works — it uses the category only as a soft score boost and to estimate per-stop visit
  time — but curated categories noticeably improve relevance. `scripts/apply_curated.py` fills
  them in without any API key.

## Out of scope

- No `/routes/ready` endpoint and no `ready_routes` table (source site has no ordered-route pages).
- No continuous pipeline — parser and enrich are one-shot scripts.
- Local LLM only does query parsing; embedding search and routing use Valhalla + pgvector.
- No multi-tab UI (the upstream isochrones / tiles tabs are not surfaced in the sidebar).
