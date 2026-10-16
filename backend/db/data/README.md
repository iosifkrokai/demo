# data — datasets and the seed contract

Data is code: every dataset is a versioned text file, and the Postgres database is
a *projection* rebuilt from them by one idempotent command. Layout: `places/`
(datasets), `geo/` (map geometry), `photos/` (photo hints + resolution), `external/`
(the contract for data an outside body sends — the reply's JSON Schema, a filled example
and the Excel form built from them by `make external-form`, not a dataset and not read by
the seed), plus `taxonomy.csv` and
`itineraries.json` — the last two are projected into the `categories` and
`itineraries`/`itinerary_stops` tables (see below), not read from disk at runtime.

Sight and service rows share the pipe-delimited 14-column shape:

```
name|category|district|town|lat|lon|blurb|fun_fact|fun_facts|opening_hours|ticket_price|visit_minutes|links|source_url
```

`fun_facts` and `links` are JSON. `opening_hours` / `ticket_price` are **reference
only** — `source_url` is what to check them against. Services carry `visit_minutes`
empty (NULL): stops *beside* a route, not sights. `places/places_curated.csv` uses
a 7-column shape (`id|normalized_name|category|blurb|fun_fact|fun_facts|links`) and
holds only the rows that reach a place by name; the prose written for a data vintage
the sets no longer carry is archived beside the datasets in `curated_retired.csv`
(see below).

`district` is **derived, not authored**: it is the polygon the point falls in, read from
`geo/districts_border.json` (OSM `admin_level=6` boundaries, ODbL, one polygon per raion
plus the city of Grodno) by `db/seed/districts.py`. `make fetch` re-derives the whole
column on every run, which is how it stays true — the column used to be "the nearest raion
centre", and a border is not a Voronoi of centres: that answer was wrong for one row in
three, including every cafe inside Grodno, which is in the city, not in the raion around
it. A point outside every polygon takes the nearest ring within 500 m (OSM's rings do not
tile perfectly), and beyond that the old nearest-centre fallback, so a row is never left
without a district. `tests/test_seed_districts.py` fails if a committed row disagrees with
the polygon it stands in.

## Two more files become tables: the taxonomy and the curated walks

The place CSVs project onto `places`; `taxonomy.csv` and `itineraries.json` project
onto reference tables the service reads instead of the files:

- **`taxonomy.csv` → `categories`, natural key `code`.** One row per canonical
  category — `code`, `ru`, `en`, `role` (`sight|service`), `osm_tags`,
  `visit_minutes`, `aliases_ru`, `aliases_en`. Written by
  `db.seed.pipeline.load_categories` with `ON CONFLICT (code) DO UPDATE`. The
  service resolves free text through the table (`db/store/taxonomy.py`); the seed
  still parses the same file (`db/seed/taxonomy.py`) only to validate a place's
  category while it writes that place.
- **`itineraries.json` → `itineraries` + `itinerary_stops`.** `itineraries` has
  natural key `id` (`id`, `title`, `blurb`, `transport`, `position`); each stop
  is an `itinerary_stops` row keyed `(itinerary_id, ordinal)`, whose `source_url`
  is the same natural key `places` is matched on — never a database id a re-seed
  could renumber. Written by `db.seed.pipeline.load_itineraries`: the walk upserts
  on `id`, `position` is the file order, and the stops are rewritten wholesale
  (delete-then-insert) so a walk that drops a stop drops it here too; the file is
  also the registry, so a walk the file no longer carries is deleted, its stops
  cascading with it. The catalogue serves them through `db/store/itineraries.py`.
  A stop is a `source_url` no seed step validates — an unknown key is dropped by
  the catalogue and shows up only as the «часть точек не нашлась» banner on a live
  stack — so `tests/test_itineraries_contract.py` checks every stop against the
  projected records, and the same file is what `quality/cases/curated/` measures the
  planner against.

Both tables, and the `areas.note` / `areas.is_project_area` columns `areas.json`
needed, arrive in the Alembic revision `0002_taxonomy_itineraries`.

## The one entry point: `python -m db.seed`

```bash
cd backend
.venv/bin/python -m db.seed --dry-run                # offline: no DB, no network
.venv/bin/python -m db.seed --report /tmp/seed.json  # apply + machine-readable report
.venv/bin/python -m db.seed fetch --source poi       # regenerate places_poi.csv
```

Order of a full apply — validate, upsert by natural key, embed locally, report:

1. `city:` / `region:` — hand-authored, `category_source = 'dataset'`; an invalid
   row is **fatal** (exit 2), so the hand-authored sets are never half-loaded.
2. `osm:` — `category_source = 'auto'`, keyed on `source_url`; invalid or
   out-of-area rows are quarantined and counted, not published (exit stays 0).
3. `osm_poi:` — same; optional (a checkout without `db.seed fetch` has no services).
4. `places_curated.csv` — matched by name, applied last, `category_source =
   'curated'`; each name also becomes a `place_aliases(locale='ru')` row. Its rows
   reach their target by name alone, so three things are deliberate: a row a
   *previous* seed marked is unmarked first (`pipeline.reset_curated`) — the flag is
   protection, and one left behind would never be rewritten again; a name several
   rows answer to is resolved by evidence, the hand-authored row first and then the
   lowest id; and a name whose candidates stand kilometres apart
   (`CURATED_TWIN_RADIUS_M`) is **not** applied to any of them — the seed prints it
   as ambiguous, because a name is not a place.

   The file holds only the rows that reach a place; a run reports zero unmatched and
   zero ambiguous for it, and a row that resolves to nothing is a bug, not a data
   vintage. The rows that reach nothing — prose written for objects the sets no
   longer carry, or for objects OSM never mapped, plus the names no rule may resolve
   — are kept verbatim in `curated_retired.csv` beside this file. That archive is
   **not a dataset**: the seed reads its datasets from explicit filenames
   (`datasets.default_datasets`, and `places_curated.csv` by name in `cli.cmd_apply`),
   never by enumerating a directory, and the archive is in none of them — so nothing
   reads it, and it is deliberately absent from `version.py`'s `VERSIONED_FILES`. The
   version digest covers the *projected* inputs; an archived row cannot change the
   projection, and hashing it would move `data_version` — and so miss the planner's
   measurement cache — on an edit that changes nothing in the database.
5. `areas` (project polygon + one row per district) and `place_sources` (one row
   per `(provider, external_id)`). The registry is the file: the polygon follows the
   row that declares it, and an area the file does not declare is deleted — an older
   seed vintage's row of the same name would otherwise resolve before the file's own
   and hand the planner a code the data disowns.
6. `categories` (from `taxonomy.csv`, key `code`) and `itineraries` +
   `itinerary_stops` (from `itineraries.json`, key `id`) — the two reference files
   projected, see above; embeddings last, from the local CPU model.
7. rows the files no longer carry are deleted — `pipeline.prune_stale`, see below.

Two rules drop a row before the upsert, both in memory (`datasets.resolve_shadowed`,
`datasets.resolve_twins`) so the report and the projection agree, and both report
in full: the report's `merged` block lists every drop, with the row that outranked
it, the distance and which rule fired.

- **A hand-authored row shadows an automatic one** of the same normalised name
  within `DUPLICATE_RADIUS_M` (150 m). The automatic row is not projected, and its
  `source_url` moves onto the hand-authored one, so `place_sources` carries both.
- **An automatic row that is the same object as another automatic row collapses**:
  same normalised name *and* category, within `TWIN_RADIUS_M` (5 m) — one stop
  mapped as a node and as a way, say. The richer row survives, ties going to the
  earlier one.

Distance is what tells a twin from a namesake, and the namesake is the common case:
the region has lines of identically named ДОТы and блиндажи a hundred metres apart,
and nearly every bus stop has a same-named counterpart across the road. The
remaining same-name pairs are left alone on purpose — collapsing on the name would
delete real objects.

Two files name a place by hand where no rule can see it, and neither is a relaxation
of the above. `place_aliases.json` gives a hand-authored row the other names it
answers to — «Парк Мир» and «Замковый комплекс Мир» are the Mir castle under another
spelling — so the first rule can shadow by a name the row does not carry; the radius
is unchanged. `place_merges.json` states one pair outright, for two rows whose names
*and* categories differ and which are still one object; the losing row is dropped and
its source kept, exactly as in the first rule. A declaration naming a row the data no
longer holds is reported in the `merged` block's `stale_declarations` rather than
silently ignored, so neither file can rot into a comment. The equivalent for the
services is not in the data but in the planner: a `role = 'service'` place — a café,
a hotel, a bus stop — is not a stop of a walk unless the request pinned it by id,
and the drops are reported.

`place_aliases_en.json` is the same shape and the same table for the other language:
the English names our OSM objects answer to, read from the local extract's `name:en`
tag — the 14-column dataset has no column for a translated name, so it travels
beside the dataset the way `place_aliases.json` does. It is generated, not typed:
`python -m db.seed aliases-en` walks a PBF, keeps the `name:en` of the objects we
hold (`osm:`/`osm_poi:` `source_url`), and writes the file; a value that repeats the
row's own name yields no alias, and the first of a semicolon list wins. Both files
land in `place_aliases` under their own `source` label, so the sweep that removes
names a file no longer declares (`DELETE ... WHERE source = <file name>`) cannot
touch the other's rows. It is versioned like the CSVs, so a regenerated file moves
the `data_version`. The seed reads it on every apply (`cli._write_registries`,
`pipeline.apply_place_aliases`); retrieval matches it through the alias signal in
`db/store/places.py::by_alias`.

`place_streets.json` is a sidecar of another kind: the street hint the rows OSM leaves
unnamed have no column for. 1273 transport stops and 303 toilets carry a generated label
(«Остановка», «Туалет») because the extract names neither, and the catalogue shows a
thousand identical rows. The file holds, per row, the nearest *named* road to its point
and the distance to it — measured from the local extract's own roads
(`python -m db.seed streets`), not a geocoder's reply — and only for the rows the answer
is unambiguous for: the road is the nearest of all roads within 15 m, is a name rather
than a road number (`Р42`), and no differently named road stands within 40 m. It is
generated, not typed. The seed overlays each hint on the row's `name` *after* the dataset
upsert (`cli._write_registries`, `pipeline.apply_place_streets`), so the CSV's own label
cannot win the name back on the next seed; the update fires only where the row still
carries the label the file extends, so a row the data has since given a real name is left
alone, and `is_fallback_name` already tolerates the extended label. The measurement
behind the file — how many rows get a street, land on a number instead of a name, or find
nothing at 15/25/50/100 m — is printed by the stage.

A third rule drops rows *after* the upsert, in the database
(`pipeline.prune_stale`): an **automatic row no dataset carries any more** is
deleted. An upsert cannot notice a row leaving the files — OSM dropping an object,
or `fetch` filtering one out as an excluded name — so without this the projection
keeps rows the CSVs do not have, forever. Two kinds survive: a hand-authored row,
and any row something still refers to, a visit (`visited_places`) or an itinerary
stop naming it by `source_url`. An absent dataset blocks the sweep entirely, since
a file nobody could read is not evidence its rows are gone. `--no-prune` keeps
them all; the count lands in the report as `stale_pruned`.

Idempotency: `places` upserts on `source_url`, `place_sources` on
`(provider, external_id)`, `place_aliases` on `(place_id, lower(alias), locale)`,
`areas` on `code`, `categories` on `code`, `itineraries` on `id` (each walk's
stops are rewritten each run) — a re-run inserts nothing new and never overwrites
a curated category. `--dry-run` builds the report from the CSVs alone, so the `db`
key is absent.

## The data version

The seed also stamps the projection's *version* into `data_versions` (one row,
`name = 'seed'`, from `data_versions` in the `0005_data_version` revision). It is
the digest of the committed files above — computed by `db/seed/version.py`, not a
constant and not an mtime — so re-running over unchanged files reproduces it, and
any change to a projected input moves it. The planner reads it through
`db/store/datasets.py` and carries it in the measurement cache key (`plan_walk`),
so a re-seed that moves a place makes the old numbers miss instead of replay. The
report carries it too (`data_version`).

## Curated categories cannot be overwritten by automatic classification

Three layers, enforced in `db/seed/pipeline.py` and pinned by
`tests/test_seed_pipeline.py`:

1. `places.category_source ∈ {curated, dataset, auto}` (the Alembic baseline,
   `backend/db/alembic/versions/0001_baseline.py`).
2. `db.seed.pipeline.upsert_sql()` keeps a protected row's category against an
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
for an "open now" or price filter, the EN names in `place_aliases_en.json` cover only
the objects OSM itself tagged `name:en`, and same-name-within-radius pairs are merge
candidates, not proof.
