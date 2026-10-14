# Roadmap

What is known and deliberately not done. Each entry says what it is, why it is parked,
and where the evidence lives — so picking one up does not mean rediscovering it.

Every entry below was checked against the working tree on 2026-10-14, and the eight that
were closed that day — the close-duplicate list, the area that did not narrow a search,
`place_aliases` unread, the benchmark's coincidence rule, the frontend's missing
`result_mode`, the English names, the service-only highlighting, and the empty
requirement vocabulary — are in § *Closed* at the bottom. The list is renumbered after
every such pass, so numbers are dense and an entry's number does not survive it.

Three of the eight entries below are waiting on a **decision**, not on hands: 2 (what to
call a boulder), 4 (populate or drop a column) and 8 (re-author or prune the curated
prose). Entry 5 is blocked by a vocabulary nobody has asked for yet. The rest are work
(3, 6, 7) or an outside block (1).

## 1. Authoritative data from the administration

The format itself is committed as data: `db/data/external/v1/schema.json` is the reply's JSON
Schema and `v1/example.json` a filled example, and an ingest would read them rather than the
workbook. They are rendered from a single source — `contract.py` and
`tools/make_schema.py` in `~/grodno-data-request/`, where the specification, the same two
files under the names the addressee sees, a read-only field reference and the list of
objects we already hold also live. The committed pair is byte-identical to that source.

The **ingest** is not built. Doing it needs:

- a new source prefix and provider (`gov:` → `grodno-admin`) and a dataset with its own
  reader — the four existing sets share a 14-column shape, and the official reply will not
  fit it;
- tables for what the schema cannot hold today at all: address, accessibility, heritage
  status and registry number, contacts, structured opening hours, admission prices,
  events, and route infrastructure;
- `place_sources.verified_at`, which exists since the baseline and has never been written —
  it is the "confirmed by the administration" slot;
- a reader for the reply: the contract is **one JSON document**, so the natural shape is a
  dataset whose reader parses JSON and walks the nested arrays (hours, prices, events) into
  child rows. If it must travel through the pipe format instead, the nested arrays go in as
  JSON columns, the way `fun_facts` and `links` already do.

## 2. `архитектура` still holds things that are not architecture

The mapping order was wrong — the two catch-all tags (`tourism=attraction`,
`historic=archaeological_site`) were read before worship and church buildings, so 419
churches, chapels and synagogues sat in `архитектура` and «покажи костёлы» could not find
them. That is fixed (`sight_tag_to_category`), and the refreshed snapshot shows what it did:
928 rows in `архитектура` today, against 2070 before the re-fetch.

What is left still contains natural objects — named boulders, trees and springs (126 rows
match boulder/stone/tree/spring words). The taxonomy has no category for those; `памятник`
is the nearest, and the choice was parked rather than guessed. A second class sits in the
same place: the close-duplicate review found nine pairs of «замок» that are World War I
pillboxes and ДОТы, swept into the castle category by the same tags — the category name is
the defect there, not the pairing.

## 3. Names for the unnamed stops

1273 transport stops and 303 toilets carry a generated label («Остановка», «Туалет»)
because OSM leaves them unnamed, and 4 of the toilets carry a street hint in the name at
best. The duplicate report skips these labels (they name a kind, not an object), which is
why they no longer pollute it, but the catalogue still shows a thousand identical rows.

Two ways out, neither taken:

- reverse geocoding per point. Prototyped, throttled by Nominatim, and removed: it
  answers a client that asks for ~1300 streets slowly, and the answers were mixed — real
  streets, road refs (`Р42`), and at least one railway platform name. The client and its
  tile cache are out of `overpass.py`; the code is in history if the idea comes back.
- a local OSM extract, which is now the proven way in: `db/seed/aliases_en.py` reads the
  350 MB `valhalla-data/belarus-latest.osm.pbf` in ten seconds with pyosmium, filtering in
  C++, and the same pass can carry `addr:street` for the stop nodes we hold. pyosmium is
  not in `pyproject.toml` (a pyproject change rebuilds the image and re-downloads the
  embedding model), so install it in the venv: `uv pip install --python .venv/bin/python
  osmium`.

## 4. A dead column

- `places.description` — declared in the schema and selected, but never written by the seed
  and never shown. Either populate it or drop it, which needs a migration: a decision
  before it is a task.

The two dead functions that sat here (`is_name_dup`, the Nominatim branch of
`resolve_district`) and the `seed prune` subcommand that nothing could satisfy any more are
deleted, not parked — pruning is the sweep over the datasets (`pipeline.prune_stale`, with
`--no-prune` to keep everything), and since 2026-10-14 `load_areas` sweeps the same way.

## 5. Steps can be avoided today; nothing asks the engine to

Valhalla's pedestrian costing takes `step_penalty` and it works. Between the two ends of
way/37560981 — a 92 m stairway tagged `handrail=yes`, `incline=down` — the route is 95 m by
default, **2.02 km** with `step_penalty=3600`, and 95 m again with `step_penalty=0`.

The obstacle is not data: the oblast holds 1560 `highway=steps` ways, about 700 of them
inside Grodno, 382 with `handrail`, 276 with `ramp`, 248 with `step_count`. The obstacle is
the promise — a detour can be twenty times longer, so "без лестниц" is only honest with its
price shown. The vocabulary for it is half there: `free` and `open` are requirement kinds
since 2026-10-14, and a `step_free` one would need the route's own edges checked for steps,
which nothing does yet. One path already forwards options like this: the frontend's own
manual-directions request (`frontend/src/utils/valhalla.ts`), which is not the planner.

Remember one trap before touching costing options: Valhalla **silently accepts unknown
`costing_options`** — `{"nonsense_option": 7}` answered 200 with the same route. A typo in
an option name would pass unnoticed, so a tune can only be checked by comparing routes,
never by the status code.

## 6. The tiles carry no elevation, so the hill options do nothing

`use_hills` and `max_grade` changed no route we tried, a 165 km one included, and the
reason is in the data: the config points at `/custom_files/elevation_data`, that directory
does not exist, and the engine has no elevation to work with — `/height` answers `null` for
every point today (the `−500.0` sentinel is what it reports for a configured but empty
directory).

Enabling it means rebuilding the valhalla image with elevation, which downloads a DEM for
the whole country; the compose service sets `tile_urls` and nothing else. Until then the
product must not promise anything about hills or climbs, and neither should the ТЗ.

## 7. What OSM already knows, which we could seed ourselves

Measured against the oblast boundary (OSM relation 59275), not against the fetch's
rectangle:

| tag | objects | what it maps to |
|---|---|---|
| `opening_hours` | 6366 | structured hours — we hold 593, and 460 of those are already in OSM syntax (`Mo-Su 10:00-22:00`, `24/7`) |
| `highway=crossing` | 6953 | the infrastructure sheet's crossings |
| `highway=steps` | 1560 | the same sheet's stairways (item 5) |
| `wheelchair` | 1729 | accessibility, every object type — an upper bound, not a count of sights |

`name:en` left this table: 295 English names are read out of the local extract and searchable
since 2026-10-14. Two of the facts we already hold used to be thrown away, and neither is
any more. `ticket_price` is the raw `fee`/`charge` tag, and the reading of it is a column
now (`places.is_free`: 107 free, 53 paid, 8390 unknown); the fifteen rows that carried a
literal `—` — which the ТЗ forbids in the reply precisely because a dash reads as a value —
are empty, and `osm_tags.ticket_price_of` refuses punctuation at the source. The 460
OSM-syntax hour strings are parsed by `core/hours.py` and read by the `open` requirement.

What is left is the **infrastructure set**: stairways, crossings, lifts and the like, which
the catalogue holds three of and the schema has a sheet for. Seeding them is what makes the
sheet in the administration's schema stop being empty, and it is the other half of item 5 —
there is nothing to highlight along a route and nothing to route around until these objects
exist.

Enrichment from OSM is a fact with a source, not a guess — but it must never be presented as
administration-confirmed. `place_sources` already keys by `(provider, external_id)` per
place, so an OSM value and an official one can sit side by side and be told apart;
`verified_at` (item 1) is the empty slot for "confirmed by".

## 8. The curated overlay reaches 17 of its 76 names

`places_curated.csv` holds 76 rows, and each reaches its target by name and nothing else —
the CSV carries no coordinate and no id. Applied on 2026-10-14: **17 matched, 58 matched
nothing at all** («Здание доминиканской школы», «Дом офицеров», «Домовая церковь Коложской
иконы Божией Матери» — prose written for a data vintage the sets no longer hold), and one
was refused as **ambiguous**: «Еврейское кладбище» is three cemeteries 70 to 190 km apart,
so `datasets.match_place` reports the name instead of putting one city's text on another's
grave. («Дом-музей Элизы Ожешко» and «Свято-Покровский кафедральный собор» resolve to the
hand-authored row standing beside their OSM twin.)

So three quarters of the curated prose — blurbs, fun facts, links — is written and lands
nowhere, and every seed says so (`curated: 17 applied of 76, 58 unmatched, 1 ambiguous`).
Two ways out, neither taken: give the CSV a `source_url` column so a row names its target
and the name becomes a hint, or prune the CSV to what the sets hold. The first helps only
where the object exists under another name — most of the 58 do not exist in the sets at all,
and re-authoring them is editorial work.

## Closed

Dropped from this list on 2026-10-14, so nobody re-adds them:

- **English names had no source.** OSM held them and the 14-column shape had nowhere to put
  them. `db/seed/aliases_en.py` reads `name:en` out of the local extract by OSM identity and
  writes a committed sidecar; 295 names land (59 were the row's own name in Latin), and a
  normal seed loads them into `place_aliases(locale='en')`, which the alias signal already
  read. «Lida castle» and «Transfiguration Church» reach their rows from an English query
  alone. Three do not resolve — their OSM rows were shadowed by a hand-authored place, and
  the Mir castle's English name is one of them, which the seed reports rather than hides.
- **Highlighting along a route was services-only.** `places_along` measures any named
  category against the line and consults no role; `services_along` stays as the service
  default the submit path and the evals call. «Что по пути» about museums answers three of
  them where it used to answer `no_service_categories`.
- **The planner had no vocabulary for what the catalogue holds.** `RequirementKind` gained
  `free` and `open`, both decided by code re-reading the measured route: `free` reads
  `ticket_price` through `core/prices.py::free_entry` (a walk must be free stop by stop — one
  paid stop is `unmet`, a price nobody wrote down `uncertain` — while a catalogue only has to
  hold free places), and `open` reads `opening_hours` through `core/hours.py`, a parser for
  the 509 of 593 rows it understands exactly and `None` for the rest. The moment is an
  argument (`PlanningDeps.now`), never a clock read inside the verifier, and both kinds are
  repairable. What is *not* closed with it: gaining a `step_free` kind, which is item 5.
- **the close-duplicate list** (`db/data/duplicate_review.md`). Read as a list at last: of the
  102 pairs closer than 30 m, **4 are one object, 91 two objects, 7 unclear**, and none of
  them is inside the city of Grodno — so the city-side risk that made this worth reading did
  not materialise. 79 of the 91 are two stop positions on one road sharing a street or
  village name, which is the «street and stop on it» case; the rest are the Smorgon line of
  World War I bunkers, mapped structure by structure. The four one-object pairs (a cemetery
  node beside its own way, a stop node beside its platform relation, a feature split by a
  raion border) are the material for a human decision, and a merge is still a hand-authored
  `place_merges.json` entry — none was made on the strength of this document alone.
- **an area did not narrow a search, and the registry answered with rows no file held.**
  `load_areas` wrote the oblast ring onto every row, so `bbox('grodno-old-town')` was the
  whole voblast; it now writes a polygon only where `areas.json` declares one and deletes the
  codes it does not declare — 19 rows of older vintages (eighteen `district:<слаг>` from a
  writer that is nowhere in the tree, one `grodno-voblast` from the `0002` revision) had been
  resolving before the file's own rows, because `resolve` answers with the first match
  ordered by `kind, code`.
- **`place_aliases` was written and never read.** `by_alias` reads it as trigrams, no model
  needed, and `find_places` ranks it as a fourth signal it reports honestly: «Парк Мир» now
  reaches «Мирский замок», which no vector signal could bring in.
- **the benchmark's coincidence rule flagged legitimate neighbours.** The rule is unchanged
  and the six pairs are named in `quality/cases/duplicate_exceptions.json`; a declared pair is
  still reported, marked `excluded`, with the reason, so the silence is visible. Replayed over
  the 2026-10-14 snapshot: `duplicate_stop` 6 pairs in 4 of 8 runs → 0, every route metric
  byte-identical.
- **the frontend had no `result_mode`.** A catalogue answer now renders as a list of places —
  count, one honest line, a card per place — with no line, no budget and no refinement
  history, and a route answer clears it.
- **a route could answer `ready` over its budget.** The self-repair attempt is in the tree
  (`1fada84`): a route the code itself called bad gets one more attempt, with the verdicts
  handed back to the model. **Not yet measured** — `quality/cases/compliance/castles_half_day.json`
  has not been re-run since, and one live `--golden` pass is what would settle it.
- **the curated overlay picking an arbitrary twin.** `match_place` takes the hand-authored
  row first, then the lowest id, and refuses a name spread over kilometres; the seed resets
  every `curated` flag before it re-applies them, so a flag left by an earlier run no longer
  protects a row for ever. What is left of it is item 8.
- **«недорого» is answerable only through the free/paid flag.** Answerable now: `places.is_free`
  is written by the seed and shown on the card, and a `free` requirement can constrain a walk.
  The administration's structured `prices` array is still the richer source, and item 1 is
  where it arrives.
- **the catalogue 500 on `result_mode: catalogue`, the three harness lies** (services,
  trajectory, and the routes layer of the one-page report), **and the Mir/Grodno
  duplicates.** Fixed; the compliance rate over two runs of a stochastic agent is
  0.750–0.917.
