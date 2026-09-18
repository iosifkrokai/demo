# Grodno walking-route POC

Minimal POC: stand up Valhalla (routing engine + demo web-app) for the Grodno region,
fill a Postgres/PostGIS/pgvector DB with sights scraped from planetabelarus.by, and serve a
FastAPI agent that turns free-text Russian queries into pedestrian routes via the same Valhalla.

## One-time setup

1. Install host prerequisites: `docker`, `docker compose`, `python3.11+`, `osmium-tool`,
   `wget`. (For the python deps: `python3 -m venv .venv && . .venv/bin/activate && pip install -r agent/requirements.txt`.)
2. Clone the Valhalla demo app as `web-app/` and edit its `.env` (one-time):
   ```bash
   git clone --depth 1 https://github.com/valhalla/web-app.git web-app
   ```
   Then replace `web-app/.env` with:
   ```
   SKIP_PREFLIGHT_CHECK=true
   VITE_VALHALLA_URL=http://localhost:8002
   VITE_NOMINATIM_URL=https://nominatim.openstreetmap.org
   VITE_TILE_SERVER_URL="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
   VITE_CENTER_COORDS="53.6772,23.8232"
   VITE_DEFAULT_COSTING_MODEL=pedestrian
   VITE_CLIENT_ID=grodno-poc
   ```
   `VITE_*` vars are baked at image build time, so this file must exist before `docker compose build`.

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

Open `http://localhost/` in a browser, place 2–3 pins anywhere in Grodno (e.g. Старый замок,
Новый замок, Каложская церковь), confirm a pedestrian route renders.

## Fill the DB

```bash
. .venv/bin/activate
python scripts/parse_places.py        # ~hundreds of sights, 5–10 min with 1.5s/request
export OPENAI_API_KEY=sk-...          # optional — without it, only embeddings populate
python scripts/enrich_places.py       # categories via LLM + local sentence-transformer embeddings
```

Spot-check the result:
```bash
psql "$DATABASE_URL" -c "SELECT count(*) AS n, count(embedding) AS with_emb, count(category) AS with_cat FROM places;"
psql "$DATABASE_URL" -c "SELECT name, lat, lon, category FROM places ORDER BY id LIMIT 20;"
```

## Run the agent

```bash
export DATABASE_URL=postgresql://grodno:grodno@localhost:5432/grodno
export VALHALLA_URL=http://localhost:8002
export OPENAI_API_KEY=sk-...          # optional
uvicorn agent.main:app --host 0.0.0.0 --port 8080
```

Smoke test:
```bash
curl -sX POST localhost:8080/routes/generate \
  -H 'content-type: application/json' \
  -d '{"query":"Хочу погулять по замкам Гродно","n_points":4}' | jq .

curl -sX POST localhost:8080/routes/reroute \
  -H 'content-type: application/json' \
  -d '{"point_ids":[1,2,3,4]}' | jq .shape | head -c 200
```

## Out of scope

No `/routes/ready`, no `ready_routes` table (source site has no ordered-route pages).
No continuous pipeline — parser and enrich are one-shot scripts.
