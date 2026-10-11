---
name: add-dataset
description: Add or change place data — new rows, a new source, a corrected category or blurb. Use when editing anything under backend/data/, when a place is wrong, or when adding a dataset.
---

# Change what is in the database

The rows come from versioned files, never from hand-written SQL. To change a
place, change the file and re-seed.

```
backend/data/
  places/   places_grodno_city.csv  places_region.csv  places_osm_raw.csv
            places_poi.csv          places_curated.csv
  geo/      areas.json  grodno_border.json  belarus_border*.json
  photos/   place_photos.json  osm_photo_hints.json
  taxonomy.csv  itineraries.json
```

Paths are resolved in `core/paths.py`; do not build them by hand.

## The row shape

Sight and service rows are pipe-delimited, 14 columns:

```
name|category|district|town|lat|lon|blurb|fun_fact|fun_facts|opening_hours|ticket_price|visit_minutes|links|source_url
```

`fun_facts` and `links` are JSON. Services (cafés, toilets, hotels) carry
`visit_minutes` empty — they are stops *beside* a route, not sights.

The natural key depends on the file: `city:` and `region:` are hand-authored,
`osm:` and `osm_poi:` come from OSM via `make fetch`. That prefix is what
idempotency is keyed on, so never invent a `source_url`.

`places_curated.csv` is different: 7 columns
(`id|normalized_name|category|blurb|fun_fact|fun_facts|links`), matched to a place
**by name**, applied last. That is where hand-written categories and blurbs belong.

## Categories are protected

`places.category_source` is `curated`, `dataset` or `auto`. Three layers keep an
automatic writer from overwriting a hand-made category:

1. the column itself;
2. the single guarded upsert (`db/seed/pipeline.py::upsert_sql`) — every dataset goes
   through it, so there is no dataset-specific SQL to bypass the guard;
3. a DB trigger (`places_guard_curated_category`) that reverts a category change
   unless the transaction opted in — only the seed does.

So: to change a category that a human set, edit `places_curated.csv`, not the OSM row.

## Before you call it done

```bash
cd backend
.venv/bin/python -m db.seed --dry-run    # the report shows what the change does
.venv/bin/python -m pytest -q tests/test_seed_pipeline.py tests/test_seed_taxonomy.py
make test-backend
```

A fatal validation failure in `places_grodno_city.csv`/`places_region.csv` exits 2
and loads nothing — the hand-authored sets never half-load. Categories must exist
in `data/taxonomy.csv`; the tests check that they all do.
