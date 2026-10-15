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

Of the seven entries below, 3 is **decided** (the column stays empty) and 5 has a decided
*no* (elevation is not worth four hours of download for a question nobody asks), so both
stay listed only as the record of a measurement. Entries 1 and 4 wait on one outside thing,
the administration's reply — 4 because its premise was measured away on 2026-10-14. Entry 6
is measured and split: seven rows are a mechanism, fifty-two are editorial. Entry 7 is a
decision nobody has made yet. The work in hand is 2.

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

## 3. A dead column — decided: it stays empty

- `places.description` — declared in the schema (`db/models/place.py`) and selected
  (`db/store/columns.py`), never written by the seed and never shown anywhere. **Decision
  (2026-10-14): leave it.** There is no source to populate it from — the prose the project
  does hold is `blurb` and `fun_facts`, written from the curated overlay and the extract —
  and dropping it would spend a migration on a column that costs a nullable field. What
  would change the decision is item 1: if the administration's reply carries descriptions,
  they arrive with their own tables and their own shape, and the slot is there for them.

The two dead functions that sat here (`is_name_dup`, the Nominatim branch of
`resolve_district`) and the `seed prune` subcommand that nothing could satisfy any more are
deleted, not parked — pruning is the sweep over the datasets (`pipeline.prune_stale`, with
`--no-prune` to keep everything), and since 2026-10-14 `load_areas` sweeps the same way.

## 4. Nothing knows whether a place is step-free — and OSM cannot tell it

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

## 5. The tiles carry no elevation, so the hill options do nothing

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

## 6. The curated overlay reaches 17 of its 76 names

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

Measured 2026-10-14, searching the extract itself for every one of the 58 names (nodes and
ways by `name`/`name:ru`/`name:be`/`old_name` inside the project area, and relations by name
alone, since relation geometry needs an area file): **6 of the 58 exist in the extract, and
52 exist nowhere in the data at all** — no relation either. The six are Grodno old-town
houses the catalogue never took in because nothing about them reads as a sight
(`way/29130252` «Дом мастера», `way/29125040` «Дом купца Муравьева», `way/29125221` «Дом
масонов», `way/29125264` «Дом врача Тальгейма», `node/320409660` «Пожарная каланча», and a
pair of nodes named «Дом офицеров»).

That splits the entry in two, and each half has a different size:

- **7 rows (6 + the ambiguous cemetery) are a mechanism**: the overlay cannot name an OSM
  object that no dataset holds. A `source_url` column would not be enough on its own — the
  row the id names does not exist — so this is "let a curated row *bring in* one extract
  object by id, the way `planner/…` reads what the sets already have". Worth doing only
  together with the other half, since 7 rows is not a reason to build it.
- **52 rows are editorial, and no mechanism helps.** «Здание доминиканской школы», «Домовая
  церковь Коложской иконы Божией Матери», «Главная хоральная синагога» — prose written for
  a vintage the sets no longer hold, or for objects OSM never mapped. The options are to
  re-author them against the sets that exist, or to accept the CSV as a record of an older
  vintage and prune it. Both are editing, not engineering; the seed names the count every
  run so the silence stays visible until someone decides.

## 7. A settings surface nothing can open

`frontend/src/components/settings-panel/settings-options.ts` is the Valhalla demo's whole
parameter catalogue — 1088 lines of Use Hills, Avoid Bad Surface, bicycle_type, truck
dimensions and the rest — with `utils/filter-profile-settings.ts` (90 lines) to cut it to
one profile, a `settings` slice plus `settingsPanelOpen` in `stores/common-store.ts`, and
`PossibleSettings` in `components/types.ts`. **No component renders any of it**: the whole
tree holds one reader of `settingsPanelOpen`, a test stub.

It is not quite dead, which is what makes it a decision rather than a deletion: the client's
hand-built route still takes its `costing_options` from those defaults
(`hooks/use-directions-queries.ts` → `filterProfileSettings(profile || 'bicycle', rawSettings)`),
and that `|| 'bicycle'` is a second oddity — a walking product whose client-side line
defaults to a cyclist. Either give the product a settings panel (which then has to say
truthfully what each knob does — item 5 is what happens when nobody checks), or delete the
catalogue and let the client route send the one option it actually needs.

Found 2026-10-14 while measuring item 5; nothing depends on it, so it stays until someone
decides which of the two it is.

## Closed

Dropped from this list on 2026-10-14, and two on 2026-10-15, so nobody re-adds them:

- **nothing asked the engine to avoid steps, and no verdict said whether the walk climbed
  one.** Closed 2026-10-15. `step_free` is a requirement kind like `free`/`open` — no code,
  no name — and the verdict is code re-reading the *measured* route against the catalogue's
  stairways (`db/store/places.py::steps_along` through the same `_beside_line` query the
  services use, at a gate of `STEP_ON_LINE_M = 1.0`). The engine is called with
  `step_penalty=3600` for a step-free pedestrian walk and with nothing for any other
  costing, and the price of the detour is reported only when the detour actually changed the
  route.

  The gate is the measurement, not the number: the existing along-route gate of 150 m
  flagged **40 of 40** measured city route-sets as having a stairway "on" them and
  `step_penalty` cleared none — it cannot tell *runs over* from *passes by*. At 1.0 m,
  **12 of those 40** run over a stairway by default and the penalty clears 10 of the 12;
  1.5 m adds 4 false positives, 2.0 m adds 8, and 1.0 m agrees exactly with Valhalla's own
  «Take the stairs» maneuver (type 40). The detour is real and priced: way/37560981 goes
  95 m → **2.02 km** (21×), 67 s → 1432 s through the app's own path.

  Three honest limits, recorded because they shape any revisit. The catalogue holds one
  point per stairway — the way's bbox centre (`db/seed/steps.py::line_center`) — so the
  measurement is point-to-line and could miss a stairway crossed away from its centre; the
  direct primitive is maneuver type 40, which needs no threshold at all, if this is ever
  redone. A catalogue answer degrades to `uncertain`/`steps_unknown`, since a list of places
  carries no stair fact, so the kind is route-only in practice. And on the city walks
  measured the price was often **0** — the default pedestrian route already avoided the
  stairway — so the 21× figure is a property of routing across that particular stairway, not
  of typical sight-to-sight walks.

  The trap that made this slow to verify is worth keeping: Valhalla **silently accepts
  unknown `costing_options`** (`{"nonsense_option": 7}` answered 200 with an identical
  route), so a tune can only be trusted by comparing routes, never by the status code.

  For the record, since history should not lie: the implementation (22 files) landed inside
  commit `e1729f2`, whose message is about one CSV column. A shared-index race — one worker
  staged path-scoped, another committed — and it was left as it is rather than rewritten,
  because four writers were live in this tree at the time.
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
  repairable. The `step_free` kind that was *not* closed with it landed the next day, in the
  entry above.
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
  protects a row for ever. What is left of it is item 6.
- **«недорого» is answerable only through the free/paid flag.** Answerable now: `places.is_free`
  is written by the seed and shown on the card, and a `free` requirement can constrain a walk.
  The administration's structured `prices` array is still the richer source, and item 1 is
  where it arrives.
- **the catalogue 500 on `result_mode: catalogue`, the three harness lies** (services,
  trajectory, and the routes layer of the one-page report), **and the Mir/Grodno
  duplicates.** Fixed; the compliance rate over two runs of a stochastic agent is
  0.750–0.917.
