# Roadmap

What is known and deliberately not done. Each entry says what it is, why it is parked,
and where the evidence lives — so picking one up does not mean rediscovering it.

## 1. Authoritative data from the administration

The format itself is committed as data: `db/data/external/v1/schema.json` is the reply's JSON
Schema and `v1/example.json` a filled example, and an ingest would read them rather than the
workbook. They are rendered from a single source — `contract.py` and
`tools/make_schema.py` in `~/grodno-data-request/`, where the specification, the same two
files under the names the addressee sees, a read-only field reference and the list of
objects we already hold also live.

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
them. That is fixed (`sight_tag_to_category`), and the re-fetch moved 321 rows out of the
category: 176 into `костёл`, 131 into `церковь`.

What is left (928 rows) still contains natural objects — named boulders, trees and
springs. The taxonomy has no category for those; `памятник` is the nearest, and the
choice was parked rather than guessed.

## 3. English names

`place_aliases(locale='en')` exists, is unused, and needs no migration — but there is no
source for it. OSM carries `name:en`; the 14-column dataset shape has nowhere to put it, so
this needs either a column or a sidecar file. Coverage today: 0 English names in the sets,
against 3370 objects tagged `name:en` inside the oblast (item 13).

## 4. The namesake list has not been reviewed

The duplicate report now separates pairs close enough to be one object mapped twice from
same-name pairs too far apart to be, and skips generated service labels. Reading the short
list is a task nobody has done: it is the only place real double mappings would show.

## 5. Highlighting along a route is services-only

`services_along` (`db/store/places.py`) orders points along the route by
`ST_LineLocatePoint` and reports `along_fraction` and `off_line_m`; it is wired into the
submit path and the map endpoint. `service_codes` (`db/store/services.py`) filters it to
`role=service`, so cafés, restaurants, toilets, hotels and stops are highlighted and
nothing else can be. Opening it to other categories is a small change, and what the ТЗ
promises the addressee about infrastructure depends on it — but for infrastructure there is
barely anything to highlight yet: the catalogue holds 3 such objects, while OSM has 1560
stairways and 6953 crossings in the oblast (item 13).

## 6. Names for the unnamed stops

1272 transport stops and 299 toilets carry a generated label («Остановка», «Туалет»)
because OSM leaves them unnamed — and untagged with a street, so the label is all there is.
The duplicate report skips these labels (they name a kind, not an object), which is why
they no longer pollute it, but the catalogue still shows a thousand identical rows.

Two ways out, neither taken:

- reverse geocoding per point. Prototyped, throttled by Nominatim, and removed: it
  answers a client that asks for ~1300 streets slowly, and the answers were mixed — real
  streets, road refs (`Р42`), and at least one railway platform name. The client and its
  tile cache are out of `overpass.py`; the code is in history if the idea comes back.
- a local OSM extract, as the photo-hints stage already uses, which answers the same
  question without a network pass.

## 7. A dead column

- `places.description` — declared in the schema and selected, but never written by the seed
  and never shown. Either populate it or drop it, which needs a migration.

The two dead functions that sat here (`is_name_dup`, the Nominatim branch of
`resolve_district`) and the `seed prune` subcommand that nothing could satisfy any more
are deleted, not parked.

## 8. Not the administration's data

Café and restaurant prices are not municipal and change daily. "недорого" stays answerable
only through the free/paid flag on municipal objects, which is why the request asks for
that flag rather than a price list.

## 9. The curated overlay does not pick a stable twin

`places_curated.csv` is applied by name — `apply_curated_rows` (`db/seed/pipeline.py`)
picks its target through `match_place` (`db/seed/datasets.py`), which returns the first
exact-name match — and the candidate list comes from an unordered
`SELECT id, name, … FROM places`. When two rows share a name (a hand-authored «Дом-музей
Элизы Ожешко» and its OSM twin), the overlay marks whichever the heap returns first, and
nothing ever unsets a `curated` row.

Measured on two applies over byte-identical files: `dataset 64 / curated 21` first, then
`dataset 62 / curated 23`. Three names now carry **both** twins as `curated` — Дом-музей
Элизы Ожешко, Свято-Покровский кафедральный собор, and Еврейское кладбище (three rows).
The upsert rewrites every row, so heap order is not stable between runs; pruning only moved
it once and made it visible.

Harmless while both classes are protected — no automatic classification overwrites either
twin — but the curated blurb, category and links land on an arbitrary one. A tie-break
fixes it: prefer the hand-authored row, or order the candidates by id.

## 10. The planner reads almost nothing the catalogue holds

Building a route consumes coordinates, the category (through the taxonomy's role and its
default visit time), `visit_minutes` when a row has one, the area polygon, and the walk
matrix (`routing/cost.py`). The rest of a place row is payload: `blurb`, `fun_fact`,
`fun_facts`, `links`, `photo_*`, `town` and `district` are displayed and never computed on,
while `opening_hours` and `ticket_price` reach the model and the card as opaque strings —
`agent/tools/places.py` projects them into the tool result, and nothing anywhere parses
them back.

What is really missing is vocabulary. `RequirementKind` is
`must_visit | service | interest | avoid` (`routing/models.py:178`), and every verdict is
reached from the presence of a place, a category code and the route geometry
(`routing/verify.py`). There is no kind for *free*, *open now*, *step-free* or *with kids*.
So the fields the administration is being asked for — accessibility, structured hours,
prices — would arrive into a planner with nowhere to put them. Putting a field in the
catalogue and letting it constrain a route are two different tasks, and only the first one
is close.

## 11. Steps can be avoided today; nothing asks the engine to

Valhalla's pedestrian costing takes `step_penalty` and it works. Between the two ends of
way/37560981 — a 92 m stairway tagged `handrail=yes`, `incline=down` — the route is 95 m by
default, **2.02 km** with `step_penalty=3600`, and 95 m again with `step_penalty=0`.

The obstacle is not data: the oblast holds 1560 `highway=steps` ways, about 700 of them
inside Grodno, 382 with `handrail`, 276 with `ramp`, 248 with `step_count`. The obstacle is
the promise — a detour can be twenty times longer, so "без лестниц" is only honest with its
price shown, and the request has no requirement kind to ask for it yet (item 10).

Remember one trap before touching costing options: Valhalla **silently accepts unknown
`costing_options`** — `{"nonsense_option": 7}` answered 200 with the same route. A typo in
an option name would pass unnoticed, so a tune can only be checked by comparing routes,
never by the status code.

## 12. The tiles carry no elevation, so the hill options do nothing

`use_hills` and `max_grade` changed no route we tried, a 165 km one included, and the
reason is in the data: the config points at `/custom_files/elevation_data`, that directory
does not exist, and `elevation_interval` returns −500.0 for every point — the engine's
"no elevation" sentinel.

Enabling it means rebuilding the valhalla image with elevation, which downloads a DEM for
the whole country; the compose service sets `tile_urls` and nothing else. Until then the
product must not promise anything about hills or climbs, and neither should the ТЗ.

## 13. What OSM already knows, which we could seed ourselves

Measured against the oblast boundary (OSM relation 59275), not against the fetch's
rectangle:

| tag | objects | what it maps to |
|---|---|---|
| `opening_hours` | 6366 | structured hours — we hold 593, and 460 of those are already in OSM syntax (`Mo-Su 10:00-22:00`, `24/7`) |
| `highway=crossing` | 6953 | the infrastructure sheet's crossings |
| `highway=steps` | 1560 | the same sheet's stairways (item 11) |
| `name:en` | 3370 | English names (item 3) |
| `wheelchair` | 1729 | accessibility, every object type — an upper bound, not a count of sights |

Two more things are already inside the catalogue and thrown away. `ticket_price` is the raw
`fee`/`charge` tag (`db/seed/osm_tags.py:240`): 73 rows say `no` — admission is free — 34 say
`yes`, and 32 more say «вход свободный». Fifteen carry a literal `—`, which the ТЗ forbids
in the reply for exactly this reason: a dash reads as a value, and ours already has them.
And the 460 OSM-syntax hour strings are a schedule waiting for a parser — the same parser
the administration's structured hours would need.

Enrichment from OSM is a fact with a source, not a guess — but it must never be presented as
administration-confirmed. `place_sources` already keys by `(provider, external_id)` per
place, so an OSM value and an official one can sit side by side and be told apart;
`verified_at` (item 1) is the empty slot for "confirmed by".

Cheapest first, if this is picked up: the free/paid flag (one column, plus deleting the
fifteen dashes), then the hours parser — which the administration's reply needs anyway —
then the infrastructure set, which is the only way the sheet in the schema stops being
empty.

## 14. An area does not narrow a search

`find_places` asks every signal for the same box: the area's bbox when an area is named,
the region's otherwise (`agent/tools/places.py:219`). `areas.json` gives a polygon to
`grodno-oblast` alone — every other entry says `"geometry": null` with a note that
geometric containment is unavailable and must never become a silent default — but
`load_areas` (`db/seed/pipeline.py`) writes the *oblast ring* into **every** row it
touches. So `bbox('grodno-old-town')` is the whole voblast, and «прогулка по Старому
городу» searches the oblast: only the ranking keeps the answer local, which is why the
golden cases still pass and why the failure is easy to miss.

The projection has to honour its own data — an area the file gives no ring is written with
none (`bbox` → `None`, and `provenance["area_filter"] = "no_boundary"` already has a slot
for that). Scoping itself needs polygons for the quarters, which is a data task, not a
code one. Measured 2026-10-14 on the seeded database (`areas` carried the oblast envelope
for all 20 registry rows).

## 15. A route can be `ready` with a total over the stated budget

`castles_half_day` («замки Гродненской области на полдня», cap 240 min) answers `ready`
with `total_minutes` of 1855, 2011 and 2437 in three runs and passes 2 of 3 repeats: the
model sometimes submits two castles 80 km apart on a *pedestrian* profile, the budget is
not a `Requirement`, and nothing in the status path reads `fits_budget`. Whether the
answer should be `degraded`, a refusal, or a walk the model was told to shorten is a
product decision nobody has made; the compliance case is what saw it
(`quality/cases/compliance/castles_half_day.json`).

## 16. `place_aliases` is written and never read

The table is populated now — from `places_curated.csv` and from `place_aliases.json`
(`pipeline.apply_place_aliases`, 27 rows as of 2026-10-14) — and no retrieval path joins
it. A search for «Мірскі замак» does not reach «Мирский замок». The seed half is done;
`name_match` / `keyword_search` reading the alias table is the rest.

## 17. The benchmark's coincidence rule flags legitimate neighbours

`coincident < 150 m` (any name) fired on 4 of 8 reference walks in the 2026-10-14 run, on
pairs that are two real objects standing close: Фарный костёл ~ Музей истории города
(102 m), часовня-усыпальница Святополк-Мирских ~ Мирский замок (119 m), Монастырь
доминиканцев ~ Историческая застройка улицы Советская (107 m). The rule was left as it
is because it also caught the two real duplicates that were fixed by hand in the same run
(the Mir complex and the Grodno museum) — narrowing it now would have hidden the pair it
was written for. Revisit once a run has no real duplicate left.

## 18. The frontend has no `result_mode`: a catalogue renders as a route

`AgentRouteResponse` carries no `result_mode`, and `sidebar.tsx` assumes a walk: with
`points.length < 2` there is nothing to draw, and a catalogue answer (60 places, `shape {}`,
`budget: null`) arrives as a route with no line and no numbers. The API half is done and
the wire announces `result_mode`; the panel needs a list-shaped answer.
