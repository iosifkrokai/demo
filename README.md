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
`tile.openstreetmap.org`, `api.deepinfra.com`. **DEEPINFRA_API_KEY is optional** —
the pipeline falls back to a regex parser when missing.

## 1. Clone repo + web-app subdir

```bash
git clone <repo-url> demo && cd demo
git clone --depth 1 https://github.com/valhalla/web-app.git web-app-fresh
# Pull our local customizations on top of the upstream clone
cp web-app-fresh/.env web-app/.env       # baseline; we patch below
cp -r web-app-fresh/node_modules web-app/ 2>/dev/null || true
rm -rf web-app-fresh
```

If `web-app/` directory already ships with custom components (sidebar.tsx, waypoint-list.tsx,
place-card-popup.tsx), keep what's in the repo — those replace the upstream equivalents.
Don't let `web-app-fresh/` overwrite them.

Then set `web-app/.env`:

```bash
cat > web-app/.env <<'ENV'
SKIP_PREFLIGHT_CHECK=true
VITE_VALHALLA_URL=http://localhost
VITE_NOMINATIM_URL=https://nominatim.openstreetmap.org
VITE_TILE_SERVER_URL="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
VITE_CENTER_COORDS="53.6772,23.8232"
VITE_DEFAULT_COSTING_MODEL=pedestrian
VITE_CLIENT_ID=grodno-poc
VITE_AGENT_URL=http://localhost:8080
ENV
```

`VITE_*` vars are baked at image build time, so this file MUST exist before `docker compose build`.

## 2. Python env (uv)

```bash
cd agent
uv sync                    # installs runtime + dev (ruff/pyright/pytest)
uv run python -c "from agent.main import app; print('OK')"
```

Pinned Python is `3.12` (see `agent/.python-version`). `uv` resolves everything
in `agent/pyproject.toml`; there is no `requirements.txt`.

## 3. Bring up Valhalla + Postgres (Docker)

```bash
cd ..   # back to repo root

# 3a. Download Belarus PBF + cut to Grodno bbox (~25 MB cut from ~580 MB)
./scripts/extract_grodno_pbf.sh

# 3b. Build & start db + valhalla (valhalla builds tiles on first start, ~5–10 min)
docker compose up -d db valhalla

# 3c. Wait for Valhalla readiness
until curl -fsS http://localhost:8002/status >/dev/null; do
    echo "waiting for valhalla..."; sleep 5
done

# 3d. Build & start the webapp
docker compose up -d --build valhalla-app
```

UI: <http://localhost/>.

The **agent service is intentionally commented out** in `docker-compose.yml` —
run it locally via `uvicorn` (step 5) so you can iterate without rebuilding images.

## 4. Seed the DB

```bash
cd agent
export DATABASE_URL=postgresql://grodno:grodno@localhost:5432/grodno

# 4a. Scrape ~76 Grodno sights from planetabelarus.by
uv run python ../scripts/parse_places.py

# 4b. Compute embeddings (always). Categories are filled by 4c, not by LLM.
uv run python ../scripts/enrich_places.py

# 4c. Apply hand-curated ground truth — names/category/blurb/fun_fact.
# MUST run last; it overwrites whatever 4a left in the DB.
uv run python ../scripts/apply_curated.py --dry-run
uv run python ../scripts/apply_curated.py
```

Spot-check:
```bash
psql "$DATABASE_URL" -c "
SELECT count(*) AS n,
       count(embedding) AS with_emb,
       count(category) AS with_cat,
       count(blurb) AS with_blurb,
       count(fun_fact) AS with_fact
FROM places;"
psql "$DATABASE_URL" -c "SELECT id, name, category FROM places ORDER BY id LIMIT 10;"
```

Expected: `n≈76`, `with_emb=76`, `with_cat=76`, `with_blurb=76`, `with_fact=76`.

## 5. Run the agent

```bash
cd agent
export DATABASE_URL=postgresql://grodno:grodno@localhost:5432/grodno
export VALHALLA_URL=http://localhost:8002
export DEEPINFRA_API_KEY=sk-...        # optional, enables structured intent extraction

uv run uvicorn agent.main:app --host 0.0.0.0 --port 8080
```

First call takes longer: it downloads the embedding model (~470 MB) into
`~/.cache/huggingface/` and the cross-encoder reranker (~2 GB) into the same
cache. Subsequent starts are instant.

## 6. Smoke tests

```bash
# Health
curl -fsS localhost:8080/health | jq

# Generate a route
curl -sX POST localhost:8080/routes/generate \
    -H 'content-type: application/json' \
    -d '{"query":"Хочу погулять по замкам Гродно","time_budget_minutes":120}' \
    | jq '.points[] | {name, category, fun_fact}'

# Re-route an explicit list
curl -sX POST localhost:8080/routes/reroute \
    -H 'content-type: application/json' \
    -d '{"point_ids":[97,98,100,101]}' \
    | jq '.summary'

# Negative constraints
curl -sX POST localhost:8080/routes/generate \
    -H 'content-type: application/json' \
    -d '{"query":"костёлы центра без музеев, 2 часа"}' \
    | jq '.points[].category'
```

## 7. Dev workflow

```bash
cd agent

# Lint (auto-fix safe issues)
uv run ruff check --fix .

# Format
uv run ruff format .

# Type-check (strict-ish, ~5 sec)
uv run pyright

# Tests (none yet — see TODO below)
uv run pytest
```

CI equivalent (run before commit):
```bash
uv run ruff check .
uv run pyright
```

## 8. Configuration knobs

All via env vars, see `agent/config.py`:

| Var | Default | Effect |
|---|---|---|
| `MMR_LAMBDA` | `0.7` | 1.0 = pure relevance, 0.0 = pure diversity |
| `RRF_K` | `60` | Reciprocal Rank Fusion constant |
| `RETRIEVAL_POOL_SIZE` | `50` | Candidates fetched into pool |
| `RERANK_POOL_SIZE` | `30` | Cross-encoder input size |
| `MMR_POOL_SIZE` | `12` | After MMR |
| `ROUTE_MAX_STOPS` | `8` | Upper bound for the route |
| `RERANK_BACKEND` | `bge` | `bge` / `off` |
| `RERANK_ENABLED` | `1` | Master switch for rerank |
| `GEMINI_MODEL` | `google/gemini-2.5-flash` | Intent extraction model |
| `BGE_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Local reranker |
| `NEGATIVE_FILTER_ENABLED` | `1` | Honour `categories_neg` from intent |

## 9. Troubleshooting

**"ConnectError" on `localhost:8002`.** Valhalla isn't ready. Poll `/status` until 200.

**"relation 'places' does not exist".** `db/init.sql` only loads on FIRST start of the `db` container. If you already have a `pgdata` volume, run `db/migrate_add_facts.sql` manually:
```bash
docker exec grodno-db psql -U grodno -d grodno \
    -c "ALTER TABLE places ADD COLUMN IF NOT EXISTS fun_fact TEXT;"
docker exec grodno-db psql -U grodno -d grodno \
    -c "ALTER TABLE places ADD COLUMN IF NOT EXISTS fun_facts TEXT;"
docker exec grodno-db psql -U grodno -d grodno \
    -c "ALTER TABLE places ADD COLUMN IF NOT EXISTS links TEXT;"
```

**MMR rerank gives all-same-category routes.** Drop `MMR_LAMBDA` to `0.5`, restart.

**BGE download stalls.** HuggingFace is rate-limiting. Pre-download:
```bash
uv run python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3')"
```

**Agent returns `503 UpstreamUnavailable` on every request.** Valhalla tile build didn't finish or `pgdata` volume lost embeddings. Check `docker logs grodno-valhalla`.

## 10. Architecture

```
User → Vite webapp (sidebar.tsx) → :8080 /routes/generate
                                       ↓
                          agent.main → agent.planner.Pipeline
                                       ↓
   ┌─ preprocess ─ intent (Gemini/DeepInfra) ─ resolve ─┐
   │                                                     │
   ├─ retrieve (multi-signal + RRF) ─ rerank (BGE) ──────┤
   │                                                     │
   ├─ diversity (MMR) ─ cost (Valhalla matrix) ─ optimize ┤
   │                                                     │
   └─ validate ─ render (Valhalla /route) ─ explain ──────┘
```

Files: `agent/planner/{preprocess,intent,resolve,retrieve,rerank,diversity,cost,optimize,validate,render,explain,pipeline}.py`.

## Out of scope

- No `/routes/ready` endpoint, no `ready_routes` table (no source for it).
- No continuous ingest pipeline — `parse_places.py`, `enrich_places.py`, `apply_curated.py` are one-shot.
- Local rerank is on CPU; turn on GPU image for ~5× speedup of cross-encoder.
