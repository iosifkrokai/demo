# Roadmap

What is known and deliberately not done. Each entry says what it is, why it is parked,
and where the evidence lives — so picking one up does not mean rediscovering it.

Nothing that is built is listed here: a closed entry leaves the file, and what closed it is
the commit that did the work. The list is renumbered after every pass, so numbers are dense
and an entry's number does not survive it.

Every entry below was checked against the working tree on 2026-10-14, except 6 and 7, which
the 2026-10-15 measurements left. Of the seven, 2 is **decided** (the column stays empty) and
4 has a decided *no* (elevation is not worth four hours of download for a question nobody
asks), so both stay listed only as the record of a measurement. Entries 1 and 3 wait on one
outside thing, the administration's reply — 3 because its premise was measured away on
2026-10-14. Entry 5 is measured and split: seven rows are a mechanism, fifty-two are
editorial. Entry 6 is what the curated layer's five live runs left: the leg it was parked on
is gone with the coordinates, and the ceiling it meets is the planner's own. Nothing below is
work in hand.

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

## 2. A dead column — decided: it stays empty

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

## 3. Nothing knows whether a place is step-free — and OSM cannot tell it

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

## 4. The tiles carry no elevation, so the hill options do nothing

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

## 5. The curated overlay reaches 17 of its 76 names

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

## 6. The curated layer's flake is the planner's tool ceiling, not the map

Re-measured 2026-10-15 with five live `--curated` runs (11 walks each, 55 runs). The leg this
entry was parked on is gone. No run flagged a leg past the engine's bound and none flagged an
empty shape, and no hop comes anywhere near the bound: `synagogues-trail`'s longest leg is
**98.8 km** (Гродно → Лида, then Лида → Слоним 87.9 km; the widest pair, Гродно ↔ Слоним, is
118.5 km) and `castles-lida-krevo-golshany`'s is 61.9 km. The 210.1 km leg was measured before
`3256c34` re-laid the region coordinates, and it does not reproduce.

What survives is the agent's own ceiling. Three of the 55 runs answered 503 «the request could
not be planned», and the agent log names the cause every time — `UsageLimitExceeded: The next
tool call(s) would exceed the tool_calls_limit of 12 (tool_calls=13)`, i.e. `MAX_TOOL_CALLS`.
Only one of the three is a regional auto walk (`synagogues-trail`); the other two are city
walks (`free-old-town`, `four-faiths`), so multi-town geometry is not what the limit catches.
`castles-lida-krevo-golshany` answered with three stops on all five runs; `synagogues-trail`
answered four times (all three towns once) and refused once, and its two-stop answers sit
inside the sibling case's own `min_places=2` (`cases/compliance/synagogues_day.json`), whose
note already records the same cap.

Neither case wants trimming: both fit the map, and the only ceiling they meet is the planner's.
So the work is the tool budget on a longer request — the budget `synagogues_day` already drops
«за день» for. Reported by the 2026-10-15 measurement, so nothing here is silent.

## 7. The fetch window is narrower than the box the data is judged against

`db/seed/overpass.py::DEFAULT_BBOX` (north 54.80) is what `make fetch` asks Overpass for;
`db/seed/datasets.py::BBOX` (north 55.01) is what the ingest accepts. The widening was
deliberate — `region:mihalishki-church` stands at 54.814 and a hand-authored row out of box is
fatal — so the fetch asks for 23 km less latitude than the product claims to cover. Nothing
committed is wrong with it today (the datasets are files, not a live refetch), but the next
`make fetch` would not see that far north.

It is the third copy of the same number and the only live one, found 2026-10-15 while deleting
`GRODNO_BBOX`, the copy nothing read. Aligning the window to the validator is one line and a
refetch — a decision rather than a task, because a refetch changes which OSM objects the
datasets would hold.
