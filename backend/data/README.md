# backend/data — datasets and the seed contract

Data is code (constitution §3). Every dataset here is a versioned text file; the
Postgres database is a *projection* built from these files by one idempotent
command. Nothing that matters lives only in the DB.

The repo has no `backend/data/README.md` yet, so this file documents the seed
contract and the coverage numbers actually measured on the current CSVs. It does
**not** hand-edit or re-generate any data row.

## Files

| File | Rows | Role | Natural-key prefix |
|---|---|---|---|
| `places_grodno_city.csv` | 18 | Hand-authored Grodno city sights | `city:` |
| `places_region.csv` | 48 | Hand-authored Grodno voblast sights | `region:` |
| `places_osm_raw.csv` | 4123 | OSM sight POIs (bbox pull, pre-geofenced) | `osm:` |
| `places_curated.csv` | 76 | Hand-labelled ground truth (id / normalized_name / category / blurb / fun_fact) | matched by **name**, no key |
| `taxonomy.csv` | — | Canonical category codes (W1) | — |
| `grodno_border.json` | — | Grodno ADM1 polygon (geoBoundaries `BY-HR`) used by the geofence | — |
| `belarus_border.json`, `belarus_border_keep.json` | — | Country polygon + documented POI exceptions | — |
| `osm_photo_hints.json` | 453 | Photo hints OSM states for the objects our points *are* (`<type>/<id>` → `wikidata`/`wikipedia`/`image`/`wikimedia_commons`) | OSM `type/id` |
| `place_photos.json` | 334 | Resolved pictures **with their attribution** (`url`, `author`, `license`, `source`, `via`) | `places.source_url` |
| `data_quality.md` | — | Why curation beats the (old, small) local classifier | — |

Every dataset row is pipe-delimited with 14 columns:

```
name|category|district|town|lat|lon|blurb|fun_fact|fun_facts|opening_hours|ticket_price|visit_minutes|links|source_url
```

`fun_facts` and `links` are JSON. `opening_hours` / `ticket_price` are
**reference only** (constitution §7) — `source_url` is the field to check them
against. `places_curated.csv` uses a different, 7-column shape (`id|normalized_name|category|blurb|fun_fact|fun_facts|links`).

## The one entry point: `scripts/seed_all.py`

```
validate  →  upsert (idempotent, by natural key)  →  coverage report
```

```bash
cd backend
./.venv/bin/python scripts/seed_all.py --dry-run                    # offline: no DB, no network
./.venv/bin/python scripts/seed_all.py --report /tmp/seed.json     # apply + machine-readable report
./.venv/bin/python scripts/seed_all.py --no-embed                  # apply, skip embeddings
```

It **reuses** the existing loaders/validators instead of re-implementing them
(`seed_region.read_rows/validate/normalize`, `load_osm.read_csv/validate/normalize`,
`apply_curated.read_curated/_match_place`); their public names are unchanged and
their behaviour is untouched.

Order of a full apply:

1. `city:` and `region:` rows — hand-authored, so `category_source = 'dataset'`.
   An invalid row here is **fatal** (exit 2): the hand-authored sets must never be
   half-loaded.
2. `osm:` rows — `category_source = 'auto'`, keyed on `source_url`. Invalid /
   out-of-area rows are quarantined and counted, not published (exit stays 0).
3. `places_curated.csv` — matched by name, applied last, written with
   `category_source = 'curated'`; each curated `normalized_name` also becomes a
   `place_aliases(locale='ru')` row.
4. `areas` — the project-area polygon from `grodno_border.json` plus one row per
   observed district; `place_sources` — one row per `(provider, external_id)`.
5. Optional embeddings — only when `OPENROUTER_API_KEY` is set and `--no-embed`
   is absent. Without a key the seed still completes and the report counts
   `pending_embeddings` (constitution §5, spec §6.5).

Idempotency: `places` upserts on the natural key `source_url`; `place_sources`
on `(provider, external_id)`; `place_aliases` on
`(place_id, lower(alias), locale)`; `areas` on `code`. Re-running inserts nothing
new and does not grow duplicates.

`--dry-run` builds the report entirely from the CSVs — it opens no connection and
makes no HTTP call. The `db` section of the report is simply absent in that mode.

## Services along a route: measured, never promised

Cafés, restaurants, toilets and hotels are **secondary points**: they may sit on
a walk, they are never what the walk is built around. `data/taxonomy.csv` owns
that split (`role = sight` vs `role = service`), the planner already refuses to
make a service a stop, and `agent/services.py` keeps the same split when it looks
_beside_ a route line.

```bash
POST /routes/services   {"shape": <GeoJSON LineString>, "profile": "pedestrian"}
```

What the answer contains, and what it deliberately does not:

* `off_line_m` — exact: PostGIS distance from the point to the route line;
* `along_m` — exact: how far along the route that point sits;
* `opening_hours` + `hours_known` — the dataset's own string, quoted as unknown
  for the ~55% of service points that have none. Nothing claims «открыто»;
* `detour_confirmed: false` and `not_measured: detour_walking_time` — the walk to
  reach a point is a real Valhalla route, which is not built here. No client may
  print «+2 мин» from this answer.

A shape that cannot be measured (a single point, a broken coordinate, a line of
2000+ points) answers 422 with a reason code, so an empty list always means
«измерили, рядом ничего нет» rather than «вход был сломан». Asking for a sight
code as a service returns `reason: no_service_categories` instead of a mixture.

## Photos: derived, licensed, and never invented

Two scripts, run in this order, each writing one file here:

```bash
cd backend
./.venv/bin/python scripts/extract_osm_photo_hints.py     # PBF + places → osm_photo_hints.json
./.venv/bin/python scripts/seed_photos.py                 # hints → Wikimedia → place_photos.json
./.venv/bin/python scripts/seed_photos.py --apply         # …and into the DB
```

`extract_osm_photo_hints.py` scans the Belarus extract and keeps only the objects
our own points claim to be — `places.source_url` is `osm:node/306067583`, so the
join is exact (3 646 of 3 712 points carry such a key) and no name/coordinate
fuzzy-matching is involved. 453 of them have a photo hint in OSM.

`seed_photos.py` turns a hint into one picture, strongest first: `wikimedia_commons`
→ Wikidata `P18` → the `wikipedia` article's lead image. Everything is resolved
through Commons `imageinfo`, which is what supplies `author`, `license` and the
file page; the image itself is stored as the 800 px rendition, not the original.

Deliberate refusals, each of which costs coverage:

* **No attribution, no photo.** A record missing `author` or `license` is dropped
  by `parse_photo`, so the API never emits a picture nobody is credited for.
* **Commons only.** The OSM `image` tag also holds share links
  (`https://photos.app.goo.gl/…`); 89 points have one and none are used, because
  there is nothing to credit.
* **A category is not a photo** (`Category:…`, or a path like `Belarus/Grodno/Farny`).
* **A URL is verified, not assumed** — the content type has to come back `image/*`.

Operational notes learned the hard way: Wikimedia answers **429** when asked too
fast, so calls retry with backoff honouring `Retry-After`, and **414** when a
batch of Cyrillic titles is put in the query string, so queries are POSTed with
the body as the cache key. Every answer is cached on disk
(`$PHOTO_CACHE`, default a scratch dir) — a second run is nearly free and mostly
re-verifies.

Attribution is a licence obligation, not decoration: the panel prints
`фото: {author} · {license}` and links to the file page.

## Curated categories cannot be overwritten by automatic classification

`enrich_places.py`'s own docstring claimed curated categories won, but its main
pass reclassified **every** row (`UPDATE places SET category = … WHERE id = …`),
so a re-run could clobber hand labels. Fixed and enforced at three layers:

1. **Provenance column** — migration `0004_places_taxonomy.sql` adds
   `places.category_source ∈ {curated, dataset, auto}` (`ADD COLUMN IF NOT
   EXISTS … NOT NULL DEFAULT 'auto'`).
2. **Writer guards** —
   * `seed_all.upsert_sql()` keeps a protected row's `category` when the incoming
     writer is automatic (`CASE WHEN places.category_source IN ('curated','dataset')
     AND EXCLUDED.category_source = 'auto'`).
   * `scripts/enrich_places.py` now selects and updates **only**
     `COALESCE(category_source,'auto') = 'auto'` rows (its two WHERE clauses were
     the minimal change needed).
3. **DB trigger** — `places_guard_curated_category` (`BEFORE UPDATE OF category`)
   reverts a category change on a curated/dataset row unless the transaction ran
   `set_config('grodno.allow_curated_category_change','on',true)`. Only
   `seed_all.py` opts in (it is the sanctioned writer and enforces priority in its
   own SQL), so `enrich_places.py` and any ad-hoc SQL can never clobber curation.

`backend/tests/test_seed_pipeline.py` pins all three (offline, no DB).

## Coverage measured on the current CSVs

Reproduced with `./.venv/bin/python scripts/seed_all.py --dry-run` (pipeline as of
the W4 branch, 2026-09-26; run it again to refresh).

| Metric | Value |
|---|---|
| Valid published records | **2531** (city 18 + region 48 + osm 2465) |
| Geofence-rejected (quarantined) | **1658** (all from `osm:`; `invalid` = 0) |
| `source_url` coverage | **2531 / 2531 = 100%** (the natural key is mandatory) |
| Coordinate coverage | **100%** (non-finite coords are rejected, never published) |
| `opening_hours` coverage | **114 / 2531 = 4.50%** — the honest blocker for any "open now" filter |
| `ticket_price` coverage | **83 / 2531 = 3.28%** |
| `visit_minutes` coverage | **100%** |
| RU-script names | 2389 / 2531 = 94.39% |
| EN-script names | **0 / 2531 = 0%** |
| Mixed-script names | 142 / 2531 = 5.61% |
| Explicit RU aliases (from curation) | **76** |
| Explicit EN aliases | **0** — no translation source yet, so unknown stays unknown |
| Suspected duplicate pairs (same name ≤150 m) | **29** (26 osm↔osm, 2 region↔osm, 1 city↔osm; only 2 within 20 m) |

Published records by category:

`архитектура` 1252 · `памятник` 557 · `замок` 295 · `костёл` 170 · `церковь` 126 ·
`музей` 96 · `храм` 18 · `монастырь` 8 · `парк` 4 · `дворец` 3 · `инфраструктура` 2

Published records by district: Гродненский 423 · Сморгонский 336 · Зельвенский 222 ·
Лидский 204 · Щучинский 163 · Новогрудский 160 · Ивьевский 148 · Кореличский 144 ·
Берестовицкий 118 · Волковысский 118 · Дятловский 108 · Островецкий 100 · Ошмянский 84 ·
Мостовский 76 · Свислочский 65 · Вороновский 38 · Гродно (город) 18 · Слонимский 3 ·
Волковыскский 3

## Honest gaps this report exposes (not fixed here)

* **Geofence rejects carry Grodno-voblast district labels.** 1658 `osm:` rows are
  rejected, and *every one* is labelled with a Grodno district —
  `Кореличский` 811, `Сморгонский` 404, `Дятловский` 260, `Зельвенский` 96,
  `Новогрудский` 47, `Ивьевский` 31, `Свислочский` 4, `Островецкий` 2,
  `Берестовицкий` 2, `Гродненский` 1 — while the *same* districts also appear
  among the published rows. Either `grodno_border.json` under-covers the voblast
  or those district labels are wrong (or both). Both the polygon and the labels
  must be checked before the reject count is treated as "foreign POIs".
  (`grodno_border.json` is owned by another workstream and was not modified.)
* **No EN names or aliases anywhere** in the datasets, so bilingual search cannot
  work yet; the report states 0% rather than inventing translations.
* **`opening_hours` 4.5% / `ticket_price` 3.3%** — too sparse to power an
  "open now" or price filter; unknown hours must stay "unknown", never "closed".
* **29 suspected duplicates** across sources are candidates for merging, not
  proof (spec §6.3); they are reported, not auto-dropped.
* **Migrations are not executed by this task** (no DB was started). Apply
  `backend/db/migrations/0004_places_taxonomy.sql` after `0002`/`0003` on a live
  DB; it is idempotent and safe to re-run.

## Report schema (`--report path.json`)

`generated_at`, `mode` (`dry-run`/`apply`), `datasets[]`, `totals`
(`records`/`invalid`/`geofence_rejects`), `by_category`, `by_district`,
`by_dataset`, `coverage` (`source_url`/`coordinates`/`opening_hours`/`ticket_price`/`visit_minutes`,
each `{count,total,share}`), `aliases`, `geofence_rejects` (`count`/`by_dataset`/`samples`),
`invalid_rows`, `suspected_duplicates` (`count`/`pairs`), `curated`, and — only in
apply mode — `db` (`places_total`, `pending_embeddings`, `by_category_source`,
`place_sources`, `place_aliases`, `areas`, `upserted`, `embedded`,
`embeddings_skipped`).
