# Working in this repo

Grodno walking-route planner: free-text Russian query → a walkable route over real
sights. Valhalla for routing, Postgres (PostGIS + pgvector) for data, a FastAPI
service, a React/Vite frontend served by nginx.

## Layout

```
backend/     the service — api (HTTP) · agent (the planner) · routing (the
             deterministic math) · core · quality · tests
             db/  — the persistence side: models (rows) · store (repositories)
                    · seed (the projection) · alembic · connection.py
                    · embeddings (the local model) · data (the datasets)
frontend/    React + Vite + MapLibre, talked to over nginx
docker-compose.yml   the whole stack, including the db image (inline)
```

`api` → `agent` → `routing` → `db` → `core`, and nothing imports upward.

`agent/` is the LLM layer and the orchestrator: the run loop, the tool surface,
the prompts, and the contracts it declares for itself (`agent/models.py::ReaderBrief`
rather than the HTTP body). The one place a provider is named is `agent/llm.py`.

`routing/` is the other leaf, below both: the Valhalla client, the cost matrix,
the optimizer, the budget arithmetic and the requirement verdicts. It imports
`core` and `db` and never `agent`, which is what lets a planning tool do
arithmetic without the agent reaching into it.

Each layer declares its own shapes: `db/models/` the rows, `routing/models.py` the
measurement's currency and the requirement contract, `api/models/` the wire. There
is no shared `contracts/` — where a rule is genuinely needed by two layers it lives
below both (`core/accounts.py` is the email/password rules the seed and the API
share, and `core/cache.py` is the reading cache).

Nothing above `db/store` writes SQL or holds a connection: a logic layer is
handed repositories (`db/store/registry.py::Repositories`) and calls them.
`tests/test_layer_boundaries.py` enforces all of that. The gate runs
`ruff format --check` as well as `ruff check`: the formatter is not optional.

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

- **Data is code.** Datasets are versioned files under `backend/db/data/`; the database
  is a *projection* built by `python -m db.seed`. Never edit rows by hand — change the
  file and re-seed. The schema is owned by Alembic (`backend/db/alembic/`), never by
  hand-written DDL.
- **Curated categories are protected.** A DB trigger plus the seed's guarded upsert
  keep an automatic writer from overwriting a `curated`/`dataset` category. If you
  add a writer, go through `db.seed.pipeline.upsert_sql`.
- **`verify` is deterministic.** Whether a route satisfies the request is decided by
  code re-reading the final route — never by asking the model again.
- **The session is the only identity.** Every surface is account-scoped — an
  anonymous visitor is sent to `/login` — and the session is an HttpOnly cookie
  (`grodno_session`) of which only `sha256(token)` is stored. There is no
  anonymous client.
- **Reading a query needs the model.** Without `OPENROUTER_API_KEY` the planning
  endpoints answer `503 llm_not_configured` rather than guessing; retrieval is
  unaffected (embeddings are local, 384-d, ONNX).
- **The product speaks Russian.** The ambiguous-unicode rules (RUF001–RUF002) stay
  on, but `allowed-confusables` in `backend/pyproject.toml` lists the Russian and
  Belarusian letters and the typography the product uses — so Cyrillic in strings
  is not a defect, while any *other* homoglyph still is.
- **Frontend:** do not fork `frontend/src/components/ui/*`; every size, radius and
  colour comes from the tokens in `frontend/src/index.css`. See `frontend/DESIGN.md`.

## Where the rest is written down

| File | For |
|---|---|
| `README.md` | what it is, endpoints, architecture, troubleshooting |
| `backend/db/data/README.md` | the seed contract: dataset schema, natural keys, protection |
| `backend/quality/README.md` | the three quality layers and how to run them |
| `frontend/DESIGN.md` | the design system |

Recurring multi-step jobs are skills under `.claude/skills/`: `seed-db`,
`add-dataset`, `run-quality`. Read the one that matches the task before improvising
the steps — each carries the failure modes that are easy to get wrong.
