# Working in this repo

Grodno walking-route planner: free-text Russian query → a walkable route over real
sights. Valhalla for routing, Postgres (PostGIS + pgvector) for data, a FastAPI
service, a React/Vite frontend served by nginx.

## Layout

```
backend/     the service — api (HTTP) · agent (LLM) · planner (the pipeline)
             contracts · domain · infra · core · quality · tests · data
             db/  — the persistence side: store (queries) · seed (the projection) · alembic
frontend/    React + Vite + MapLibre, talked to over nginx
docker-compose.yml   the whole stack, including the db image (inline)
```

`api` → `planner`/`agent`/`db.store`; `planner` → `agent`/`db.store`/`infra`;
nothing imports upward. `agent/` is the LLM layer (model, prompts, tools, runner) —
the one place a provider is named is `agent/model.py`.

## Bring it up

```bash
make up        # build + start db, valhalla, agent, frontend (creates .env first)
make seed      # apply the committed data — idempotent, safe to re-run
make migrate   # apply the schema (Alembic)
make help      # everything else
```

The Valhalla tile build on first start is slow; the embedding model is baked into
the image at build time, so there is no first-call download.

## Gates — run these, do not guess

```bash
make test            # both halves
make test-backend    # ruff · pyright · pytest · the offline seed contract
make test-frontend   # typecheck · eslint · vitest
```

Python is managed by `uv` in `backend/` (there is no `requirements.txt`), pinned to
3.12. Live tests skip themselves when the stack is not reachable — a skip is not a
pass you should ignore.

## Things that are not negotiable

- **Data is code.** Datasets are versioned files under `backend/data/`; the database
  is a *projection* built by `python -m db.seed`. Never edit rows by hand — change the
  file and re-seed. The schema is owned by Alembic (`backend/db/alembic/`), never by
  hand-written DDL.
- **Curated categories are protected.** A DB trigger plus the seed's guarded upsert
  keep an automatic writer from overwriting a `curated`/`dataset` category. If you
  add a writer, go through `db.seed.pipeline.upsert_sql`.
- **`verify` is deterministic.** Whether a route satisfies the request is decided by
  code re-reading the final route — never by asking the model again.
- **Reading a query needs the model.** Without `OPENROUTER_API_KEY` the planning
  endpoints answer `503 llm_not_configured` rather than guessing; retrieval is
  unaffected (embeddings are local, 384-d, ONNX).
- **The product speaks Russian.** `ruff` deliberately ignores the ambiguous-unicode
  rules (RUF001–RUF003): Cyrillic in strings is the point, not a defect.
- **Frontend:** do not fork `frontend/src/components/ui/*`; every size, radius and
  colour comes from the tokens in `frontend/src/index.css`. See `frontend/DESIGN.md`.

## Where the rest is written down

| File | For |
|---|---|
| `README.md` | what it is, endpoints, architecture, troubleshooting |
| `backend/data/README.md` | the seed contract: dataset schema, natural keys, protection |
| `backend/quality/README.md` | the three quality layers and how to run them |
| `frontend/DESIGN.md` | the design system |

Recurring multi-step jobs are skills under `.claude/skills/`: `seed-db`,
`add-dataset`, `run-quality`. Read the one that matches the task before improvising
the steps — each carries the failure modes that are easy to get wrong.
