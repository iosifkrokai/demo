# backend/data — datasets and the seed contract

Data is code (`docs/specs/constitution.md` §3). Every dataset here is a versioned
text file; the Postgres database is a *projection* built from these files by one
idempotent command.

## Files

| File | Role | Natural key (prefix) |
|---|---|---|
| `places_grodno_city.csv` | Hand-authored Grodno city sights | `city:` |
| `places_region.csv` | Hand-authored Grodno voblast sights | `region:` |
| `places_osm_raw.csv` | OSM sight POIs (bbox pull, pre-geofenced) | `osm:` |
| `places_poi.csv` | OSM everyday services (cafes, toilets, …) — written by `seed fetch` | `osm_poi:` |
| `places_curated.csv` | Hand-labelled ground truth (category/blurb/fun_fact) | matched by **name** |
| `taxonomy.csv` | Canonical category codes (role: sight/service) | — |
| `grodno_border.json` | Grodno ADM1 polygon used by the geofence | — |
| `belarus_border.json`, `belarus_border_keep.json` | Country polygon + documented POI exceptions | — |
| `osm_photo_hints.json` | Photo tags of the OSM objects our points are | `type/id` |
| `place_photos.json` | Resolved pictures **with attribution** | `places.source_url` |
| `areas.json`, `itineraries.json` | Area registry + hand-written itineraries | — |

Sight and service rows share the pipe-delimited 14-column shape:

```
name|category|district|town|lat|lon|blurb|fun_fact|fun_facts|opening_hours|ticket_price|visit_minutes|links|source_url
```

`fun_facts` and `links` are JSON. `opening_hours` / `ticket_price` are **reference
only** (constitution §7) — `source_url` is the field to check them against.
Services carry `visit_minutes` empty (NULL): they are stops *beside* a route, not
sights. `places_curated.csv` uses a 7-column shape
(`id|normalized_name|category|blurb|fun_fact|fun_facts|links`).

## The one entry point: `python -m seed`

```
validate  →  upsert (idempotent, by natural key)  →  embed (local)  →  coverage report
```

```bash
cd backend
.venv/bin/python -m seed --dry-run                # offline: no DB, no network
.venv/bin/python -m seed --report /tmp/seed.json  # apply + machine-readable report
.venv/bin/python -m seed --no-embed               # apply, skip embeddings
.venv/bin/python -m seed fetch --source poi       # regenerate places_poi.csv
```

Order of a full apply:

1. `city:` / `region:` rows — hand-authored, `category_source = 'dataset'`. An
   invalid row here is **fatal** (exit 2): the hand-authored sets must never be
   half-loaded.
2. `osm:` rows — `category_source = 'auto'`, keyed on `source_url`. Invalid or
   out-of-area rows are quarantined and counted, not published (exit stays 0).
3. `osm_poi:` rows — same treatment; the file is optional (a fresh checkout without
   `seed fetch` simply has no services).
4. `places_curated.csv` — matched by name, applied last, `category_source =
   'curated'`; each curated name also becomes a `place_aliases(locale='ru')` row.
5. `areas` (project polygon + one row per district) and `place_sources` (one row per
   `(provider, external_id)`).
6. Embeddings — local CPU model (`agent/embeddings.py`), always available.

Idempotency: `places` upserts on `source_url`; `place_sources` on
`(provider, external_id)`; `place_aliases` on `(place_id, lower(alias), locale)`;
`areas` on `code`. Re-running inserts nothing new and never overwrites a curated
category.

`--dry-run` builds the report entirely from the CSVs — no connection, no HTTP call;
the `db` key is simply absent.

## Curated categories cannot be overwritten by automatic classification

Three layers, enforced in `seed/pipeline.py` and pinned by
`tests/test_seed_pipeline.py`:

1. `places.category_source ∈ {curated, dataset, auto}` (migration `0004`).
2. The single `seed.pipeline.upsert_sql()` keeps a protected row's category against
   an automatic writer (`EXCLUDED.category_source = 'auto'`); the row's own
   authoritative dataset can still refresh it.
3. A DB trigger (`places_guard_curated_category`) reverts a category change on a
   curated/dataset row unless the transaction ran
   `set_config('grodno.allow_curated_category_change','on',true)`. Only `seed` opts
   in (it is the sanctioned writer), so ad-hoc SQL can never clobber curation.

## Photos

```bash
.venv/bin/python -m seed photos                 # hints → Wikimedia → place_photos.json
.venv/bin/python -m seed photos --apply         # …and into the DB
```

Resolution order is `wikimedia_commons` → Wikidata `P18` → the `wikipedia` article's
lead image, all through Commons `imageinfo` (which supplies `author`, `license`,
file page). Deliberate refusals, each costing coverage: **no attribution → no
photo**; Commons only (OSM share links are not credited); a `Category:` is not a
photo; a URL is only used once the content type comes back `image/*`.

## Coverage

Re-run `python -m seed --dry-run` (or `--report`) to refresh the numbers: records
by category/district/source, RU/EN name-script coverage, opening-hours/ticket
coverage, geofence rejects, suspected duplicates, pending embeddings. The report is
honest — unknown stays unknown, and rejects are quarantined and counted rather than
silently published.

Known gaps the report exposes: `opening_hours`/`ticket_price` are sparse (too thin
to power an "open now" or price filter), there are no EN names/aliases yet, and
same-name-within-radius pairs are candidates for merging, not proof. See
`data_quality.md` for why curation beats the classifier, and
`docs/specs/002-grodno-guide-rebuild/tasks.md` (W4) for the open data work.
