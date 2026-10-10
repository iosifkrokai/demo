# backend/data — datasets and the seed contract

Data is code: every dataset is a versioned text file, and the Postgres database is
a *projection* rebuilt from them by one idempotent command. Layout: `places/`
(datasets), `geo/` (map geometry), `photos/` (photo hints + resolution), plus
`taxonomy.csv` and `itineraries.json`.

Sight and service rows share the pipe-delimited 14-column shape:

```
name|category|district|town|lat|lon|blurb|fun_fact|fun_facts|opening_hours|ticket_price|visit_minutes|links|source_url
```

`fun_facts` and `links` are JSON. `opening_hours` / `ticket_price` are **reference
only** — `source_url` is what to check them against. Services carry `visit_minutes`
empty (NULL): stops *beside* a route, not sights. `places/places_curated.csv` uses
a 7-column shape (`id|normalized_name|category|blurb|fun_fact|fun_facts|links`).

## The one entry point: `python -m seed`

```bash
cd backend
.venv/bin/python -m seed --dry-run                # offline: no DB, no network
.venv/bin/python -m seed --report /tmp/seed.json  # apply + machine-readable report
.venv/bin/python -m seed fetch --source poi       # regenerate places_poi.csv
```

Order of a full apply — validate, upsert by natural key, embed locally, report:

1. `city:` / `region:` — hand-authored, `category_source = 'dataset'`; an invalid
   row is **fatal** (exit 2), so the hand-authored sets are never half-loaded.
2. `osm:` — `category_source = 'auto'`, keyed on `source_url`; invalid or
   out-of-area rows are quarantined and counted, not published (exit stays 0).
3. `osm_poi:` — same; optional (a checkout without `seed fetch` has no services).
4. `places_curated.csv` — matched by name, applied last, `category_source =
   'curated'`; each name also becomes a `place_aliases(locale='ru')` row.
5. `areas` (project polygon + one row per district) and `place_sources` (one row
   per `(provider, external_id)`); embeddings last, from the local CPU model.

Idempotency: `places` upserts on `source_url`, `place_sources` on
`(provider, external_id)`, `place_aliases` on `(place_id, lower(alias), locale)`,
`areas` on `code` — a re-run inserts nothing new and never overwrites a curated
category. `--dry-run` builds the report from the CSVs alone, so the `db` key is
absent.

## Curated categories cannot be overwritten by automatic classification

Three layers, enforced in `seed/pipeline.py` and pinned by
`tests/test_seed_pipeline.py`:

1. `places.category_source ∈ {curated, dataset, auto}` (the Alembic baseline,
   `backend/alembic/versions/0001_baseline.py`).
2. `seed.pipeline.upsert_sql()` keeps a protected row's category against an
   automatic writer (`EXCLUDED.category_source = 'auto'`); the row's own
   authoritative dataset can still refresh it.
3. The `places_guard_curated_category` trigger reverts a category change on a
   curated/dataset row unless the transaction ran
   `set_config('grodno.allow_curated_category_change','on',true)`; only `seed`
   opts in, so ad-hoc SQL can never clobber curation.

## Known gaps

`--report` / `--dry-run` refresh the coverage numbers (by category/district/source,
RU/EN name-script, opening-hours/ticket, geofence rejects, duplicates, pending
embeddings); the report is honest — unknown stays unknown, rejects are counted, not
published. Known gaps it exposes: `opening_hours` / `ticket_price` are too sparse
for an "open now" or price filter, there are no EN names/aliases yet, and
same-name-within-radius pairs are merge candidates, not proof.
