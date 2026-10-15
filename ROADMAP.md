# Roadmap

What is known and deliberately not done. Each entry says what it is, why it is parked,
and where the evidence lives — so picking one up does not mean rediscovering it.

Nothing that is built is listed here: a closed entry leaves the file, and what closed it is
the commit that did the work. The list is renumbered after every pass, so numbers are dense
and an entry's number does not survive it.

Every entry below was checked against the working tree on 2026-10-14. Of the nine, 2 is
**decided** (the column stays empty) and 4 has a decided *no* (elevation is not worth four
hours of download for a question nobody asks), so both stay listed only as the record of a
measurement. Entries 1 and 3 wait on one outside thing, the administration's reply — 3
because its premise was measured away on 2026-10-14. Entry 5 is measured and split: seven
rows are a mechanism, fifty-two are editorial. Entries 6 to 9 are what the 2026-10-15
measurements left behind: a settings surface nothing can open, an English name a shadowed
row loses, a box that disagrees with the box beside it, and two curated walks that ask for
more than the product gives. Nothing below is work in hand.

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

## 6. A settings surface nothing can open

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
truthfully what each knob does — item 4 is what happens when nobody checks), or delete the
catalogue and let the client route send the one option it actually needs.

Found 2026-10-14 while measuring item 4; nothing depends on it, so it stays until someone
decides which of the two it is.

## 7. A shadowed row loses its English name

`db/data/place_aliases_en.json` reaches a row by its own `source_url`, and a row the seed
has shadowed — dropped from `places` when a hand-authored row claims the same place — is not
there to be found. Every seed says so (`aliases_en_written: 292 written, 295 declared,
unresolved: [...]`) and **four are unresolved today**: `osm:relation/1579104` («Mir Castle»),
`osm:relation/5755161` («Lida castle», shadowed the moment the Lida row's coordinates were
corrected) and two others. So two of the country's best-known castles answer an English
query by name only if the Russian one happens to match.

The fix is not in the JSON: after `apply_shadowed` the loser's identity is gone from
`places` but not from `place_sources`, which still holds `external_id` and the provider.
Resolving the alias through the survivor's `place_sources` row would attach «Mir Castle» to
`region:mir-castle`, where the visitor looking for it belongs. Found 2026-10-15 by the
districts/curated work; reported honestly by the seed in the meantime, so nothing is silent.

## 8. A box that no longer agrees with the box next to it

`core/constants.py::GRODNO_BBOX` says the north edge is 54.80; `db/seed/datasets.py::BBOX`
says 55.01. Only one of them is read — the seed's, by the ingest — and the other is **read
by nothing at all**: it is re-exported into `quality/runner.py` and never used there or
anywhere else. The widening was deliberate (a hand-authored row, `region:mihalishki-church`
at 54.814, is fatal when out of box), so the number that moved is the right one and the
constant that stayed is a trap: whoever next reaches for "the area the product covers" gets
a box 23 km narrower than the one the data was fetched with.

Either align it with the seed's or delete it as the dead constant it is. A decision, not a
task — the same shape as item 2.

One correction of the record, since a commit message cannot be rewritten: `fc4cfcb` took
the widened line as "nobody claims it". The owner came forward the same day — it is the
region-coordinate work's, and its reason is better than the one I inferred: a hand-authored
row out of box is fatal, and `region:mihalishki-church` is legitimately at 54.814. So the
widening stays, the steps test I re-pinned to the southern residue stays right, and only the
provenance was wrong.

## 9. Two of the eleven curated walks ask for more than the product gives

The curated layer ran twice on 2026-10-15 and gated a different run out each time: once
`castles-lida-krevo-golshany` came back "the request could not be planned" (`api_error`) and
once `synagogues-trail` returned an empty shape (`geometry_missing`) — both times on the
regional **auto** walks, never on the six city ones. The same run also flagged a leg of
**210.1 km** between two stops of `synagogues-trail` against the engine's own **180 km** cap
for motorised transport (the cap is the drive's four-hour fallback at its own speed, and it
is the product's, not the harness's).

So the case asks for something the product refuses: Гродно → Лида → Слоним cannot be one
leg inside the cap, and «замки Лиды, Крево и Гольшан за день» is close to the same edge.
That is either a case that wants trimming to what the product serves, or a cap that is wrong
for a day's drive — a decision, and the second run's numbers are the evidence for it.
