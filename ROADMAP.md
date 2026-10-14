# Roadmap

What is known and deliberately not done. Each entry says what it is, why it is parked,
and where the evidence lives — so picking one up does not mean rediscovering it.

Every entry below was checked against the working tree on 2026-10-14, and the ten that
were closed that day — the close-duplicate list, the area that did not narrow a search,
`place_aliases` unread, the benchmark's coincidence rule, the frontend's missing
`result_mode`, the English names, the service-only highlighting, the empty requirement
vocabulary, the names for the unnamed stops, and the empty infrastructure sheet — are in
§ *Closed* at the bottom. The list is renumbered after every such pass, so numbers are
dense and an entry's number does not survive it.

Two of the seven entries below are waiting on a **decision**, not on hands: 2 (what to call
a boulder) and 3 (populate or drop a column). Entry 7 is editorial. Entries 1 and 5 are
waiting on one outside thing, the administration's reply — 5 because its premise was
measured away on 2026-10-14. The rest are work (4, 6).

## 1. Authoritative data from the administration

The format itself is committed as data: `db/data/external/v1/schema.json` is the reply's JSON
Schema and `v1/example.json` a filled example, and an ingest would read them rather than the
workbook. They are rendered from a single source — `contract.py` and
`tools/make_schema.py` in `~/grodno-data-request/`, where the specification, the same two
files under the names the addressee sees, a read-only field reference and the list of
objects we already hold also live. The committed pair is byte-identical to that source.

The **ingest** is not built. Doing it needs:

- a new source prefix and provider (`gov:` → `grodno-admin`) and a dataset with its own
  reader — the four hand-authored and OSM sets share a 14-column shape, and the official
  reply will not fit it;
- tables for what the schema cannot hold today at all: address, accessibility, heritage
  status and registry number, contacts, structured opening hours, admission prices,
  events, and route infrastructure;
- `place_sources.verified_at`, which exists since the baseline and has never been written —
  it is the "confirmed by the administration" slot;
- a reader for the reply: the contract is **one JSON document**, so the natural shape is a
  dataset whose reader parses JSON and walks the nested arrays (hours, prices, events) into
  child rows. If it must travel through the pipe format instead, the nested arrays go in as
  JSON columns, the way `fun_facts` and `links` already do.

Hours and prices are no longer the obstacle they were: the catalogue has a parser
(`core/hours.py`) and a fact (`places.is_free`) for both, so an official value has
somewhere to land and something to compare against.

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

## 3. A dead column

- `places.description` — declared in the schema and selected, but never written by the seed
  and never shown. Either populate it or drop it, which needs a migration: a decision
  before it is a task.

The two dead functions that sat here (`is_name_dup`, the Nominatim branch of
`resolve_district`) and the `seed prune` subcommand that nothing could satisfy any more are
deleted, not parked — pruning is the sweep over the datasets (`pipeline.prune_stale`, with
`--no-prune` to keep everything), and since 2026-10-14 `load_areas` sweeps the same way.

## 4. Steps can be avoided today; nothing asks the engine to

Valhalla's pedestrian costing takes `step_penalty` and it works. Between the two ends of
way/37560981 — a 92 m stairway tagged `handrail=yes`, `incline=down` — the route is 95 m by
default, **2.02 km** with `step_penalty=3600`, and 95 m again with `step_penalty=0`.

Both halves of the obstacle are now smaller. The **data** exists: 1556 stairways are in the
catalogue since 2026-10-14 (`places_steps.csv`, `highway=steps`, 633 tagged `handrail`, 479
`ramp`, 464 `step_count`), highlighted beside a route and never a stop of one. The
**vocabulary** has a shape to copy: `free` and `open` are requirement kinds, and a
`step_free` one would be the third. What is missing is the verdict — nothing checks the
route's own edges for steps, so a `step_free` claim would be about the costing option and
not about the walk — and the promise: a detour can be twenty times longer, so "без лестниц"
is only honest with its price shown.

Remember one trap before touching costing options: Valhalla **silently accepts unknown
`costing_options`** — `{"nonsense_option": 7}` answered 200 with the same route. A typo in
an option name would pass unnoticed, so a tune can only be checked by comparing routes,
never by the status code.

## 5. Nothing knows whether a place is step-free — and OSM cannot tell it

The administration's schema asks for accessibility and the catalogue answers nothing, so
reading OSM's `wheelchair` tag looked like the same shape of task as the stairways: extract
the tag, hold it as a fact, decide a requirement by it. Measured on 2026-10-14 before it
was built (the same `KeyFilter` pass the stairways use), the move does not carry.

The extract holds **16051** wheelchair-tagged objects, **1723** of them inside the project
area, and **183** of those map to our categories at all — of which **179** are rows in the
catalogue today: **84 туалет, 43 кафе, 18 ресторан, 8 гостиница, 3 остановка**, and on the
sight side **8 архитектура, 5 костёл, 4 церковь, 3 памятник, 3 музей** — **23 sights**, none
of them a замок or a монастырь. The other 1540 are objects the catalogue deliberately does
not hold: 1098 match nothing we map (benches, vending machines, waste baskets, bicycle
parking), then footways, convenience shops, pharmacies, banks, ATMs, crossings and parking
spaces. And what *is* stated is not clean: 23 objects read `wheelchair=дд`, one `Киоск`,
one carries opening hours in the field.

So the fact is cheap and would still answer "unknown" for 98% of the catalogue — a
`wheelchair_access` requirement decided by it would look measured while resting on two
dozen sights and a hundred toilets. Two further reasons not to promise anything from the
tag: it is an upper bound rather than a count of sights (it is stated on benches), and
`yes` does not mean step-free (a ramp at the door does not cover the stairs inside).

The source that can carry this is item 1: the administration's schema has an accessibility
block, so the fact arrives on the places that matter, with a body behind it. Until then the
product says nothing about accessibility, and neither should the ТЗ.

## 6. The tiles carry no elevation, so the hill options do nothing

`use_hills` and `max_grade` changed no route we tried, a 165 km one included. There are two
reasons, and the second makes the first moot for a walk:

- **Nobody is offering them.** The backend never sends either option — the only
  `costing_options` it composes is `routing/valhalla/constants.py::costing_options_for`, for
  a step-free walk. The frontend *carries* the Valhalla demo's parameter catalogue
  (`settings-panel/settings-options.ts`, 1088 lines: Use Hills, Avoid Bad Surface,
  bicycle_type…, with `filterProfileSettings` and a `settingsPanelOpen` slice in
  `common-store`), and **no component renders any of it**: `settingsPanelOpen` is read by
  one test stub and by nothing else in `frontend/src`. So the control a user would turn does
  not exist — the product is silent about hills by construction, not by luck, and what is
  left is dead weight (a cleanup item of its own).
- **Both are the bicycle costing's options**, described in their own text as "a cyclist's
  desire to tackle hills", while a walk is priced `pedestrian`. Had the DEM been there, a
  pedestrian experiment would still have shown nothing — and Valhalla silently accepts
  unknown costing options, so the mismatch passes for a working knob.

The data side is as measured before: the config points at `/custom_files/elevation_data`,
that directory does not exist, and `/height` answers `null` for every point today — the
`−500.0` sentinel is what it reports for a configured but empty directory.

The cost of enabling it has numbers now (2026-10-14): one skadi cell (1°×1°) is **~4.5 MB**,
12 cells cover the oblast and about 60 the PBF's bbox, and the `elevation-tiles-prod`
bucket served **459 KB in 25 s (18 KB/s)** — roughly **50 minutes** of download for the
oblast, **4 hours** for the country, plus a tile rebuild and a valhalla restart with no
routing while it runs. Measured and declined (2026-10-14): not worth it for a product that
asks no hill question, and the ТЗ must not ask one either.

## 7. The curated overlay reaches 17 of its 76 names

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

- **names for the unnamed stops.** 1273 stops and 303 toilets carried a generated label
  because OSM leaves them unnamed, and `addr:street` — which `service_name` already read —
  is stated on 4 of them. The remaining path, a nearest-street guess, was measured before it
  was believed: of 1572 targets, 818 sit on a **road number** within 15 m (`Р42`, `М6`,
  `Н6131` — the failure that made the Nominatim answers mixed) and 501 have no road that
  close, leaving **210** unambiguous (one named street within 15 m, no differently named
  street within 40 m), which the seed lays on as «Остановка (улица Адама Мицкевича)» —
  201 stops and 8 toilets. The ~87% that get nothing are not a gap in the code: there is no
  street to name them after, and inventing one is what this measurement refuses to do.
- **the infrastructure sheet was empty.** Three objects against 1557 stairways in the
  oblast. `places_steps.csv` brings them in from the local extract with the tags OSM states
  as facts, `role = service` so they are highlighted beside a walk and never a stop of one.
  Seeding them exposed a second defect with it: the default highlight was "every
  `role = service` category", so the map would have shown stairs instead of cafés; the
  default is now a column in `taxonomy.csv` the seed projects. **Crossings are not in and
  must not be added quietly** — 6953 of them would grow the catalogue by 80% and serve no
  walk; the ТЗ sheet is not a reason to make them places.
- **English names had no source.** OSM held them and the 14-column shape had nowhere to put
  them. `db/seed/aliases_en.py` reads `name:en` out of the local extract by OSM identity and
  writes a committed sidecar; 295 names land (59 were the row's own name in Latin), and a
  normal seed loads them into `place_aliases(locale='en')`, which the alias signal already
  read. «Lida castle» and «Transfiguration Church» reach their rows from an English query
  alone. Three do not resolve — their OSM rows were shadowed by a hand-authored place, and
  the Mir castle's English name is one of them, which the seed reports rather than hides.
- **highlighting along a route was services-only.** `places_along` measures any named
  category against the line and consults no role; `services_along` stays as the service
  default the submit path and the evals call. «Что по пути» about museums answers three of
  them where it used to answer `no_service_categories`.
- **the planner had no vocabulary for what the catalogue holds.** `RequirementKind` gained
  `free` and `open`, both decided by code re-reading the measured route: `free` reads
  `ticket_price` through `core/prices.py::free_entry` (a walk must be free stop by stop — one
  paid stop is `unmet`, a price nobody wrote down `uncertain` — while a catalogue only has to
  hold free places), and `open` reads `opening_hours` through `core/hours.py`, a parser for
  the 509 of 593 rows it understands exactly and `None` for the rest. The moment is an
  argument (`PlanningDeps.now`), never a clock read inside the verifier, and both kinds are
  repairable. What is *not* closed with it: a `step_free` kind, which is item 4.
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
  protects a row for ever. What is left of it is item 7.
- **«недорого» is answerable only through the free/paid flag.** Answerable now: `places.is_free`
  is written by the seed and shown on the card, and a `free` requirement can constrain a walk.
  The administration's structured `prices` array is still the richer source, and item 1 is
  where it arrives.
- **the catalogue 500 on `result_mode: catalogue`, the three harness lies** (services,
  trajectory, and the routes layer of the one-page report), **and the Mir/Grodno
  duplicates.** Fixed; the compliance rate over two runs of a stochastic agent is
  0.750–0.917.
