# The hand-typed region points — what the polygon says, and what nobody checked

## What this is

`places/places_region.csv` is hand-authored: the name, the category, the town and the
blurb are written by a person, and so is the `lat`/`lon` in columns 5 and 6. Nothing in
the seed looks at that point. The row is projected wherever it says it is, and the card,
the map pin and every routed leg are built on it — a typed pair of numbers is a
measurement the product never re-takes.

This is the review of that column, written after correcting it on **2026-10-15**. It is
not a change to the contract: it records what was checked, what was found, what the fix
rests on, and which rows still want a human.

## Why it was opened

A measured leg gave the game away. `Дворец Огинских (Слоним) → Большая синагога
(Слоним)` came back from Valhalla as **4.1 km by car, 95 minutes**, inside one small
town, for two objects that stand a few hundred metres apart. The rows were the problem,
not the engine: both points sat ~41 km north-east of Slonim, in the middle of a field
between Navahrudak and Mir.

The same audit found the same shape elsewhere: 15 of the 48 rows stood outside the
raion the row itself declares, and the worst of them — Крево 37.8 km out, Свислочь
55 km, Сынковичи 70.6 km — are objects any map places in one town.

## What a point in this file can be checked against

Three sources, in descending strength:

1. **The raion polygon** (`db/seed/districts.py` over `geo/districts_border.json`,
   OSM `admin_level=6`). Does the point fall inside the raion the row's own `district`
   column names? Offline, no network, and already gating:
   `tests/test_seed_districts.py::test_the_committed_districts_are_the_ones_the_boundaries_give`.
   This check is what stopped the file being able to lie quietly.
2. **Our own OSM rows** (`places_osm_raw.csv`): does a point of the same object stand
   within the shadow radius (150 m)? A weaker test than it sounds — the hand-authored
   rows name things their own way («Костёл Святого Николая (Мир)» against OSM's «Костёл
   Святого Николая»), so a miss here is not evidence of a wrong point.
3. **The local extract** (`valhalla-data/belarus-latest.osm.pbf`, 350 MB, the file
   Valhalla builds its tiles from), read offline with pyosmium. This is what most of the
   corrections rest on. Its limit is worth stating: the extract names these objects in
   **Belarusian** where the rows are Russian («Касцёл святога Міхаіла Архангела»), so a
   re-run that matches Russian spellings will not see them, and their absence from a
   search is not evidence that the extract lacks the object.

No network was used: no geocoder, no Wikipedia, no Nominatim. The evidence is the data
in this repository plus the extract the stack already ships.

## The check, and the number it gives

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_seed_districts.py
```

Before the correction the polygon disagreed with 15 of the 48 committed rows; after it,
the number is **0**, and the test holds it there. The disagreement was not a near-miss:
`Дворец Огинских (Слоним)`, `Большая синагога (Слоним)` and `Костёл Непорочного
Зачатия Девы Марии (Слоним)` were all outside the Slonim raion, `Кревский замок` outside
the Ашмянскі raion it declares, `Костёл Пресвятой Троицы (Свислочь)` 55 km from Svislach.

## What changed — 36 rows

Each row below had its `lat`/`lon` replaced; the columns after the name are the point
that was there and the point that is now, and how far apart they are. Everything else in
the row (name, category, district, town, blurb, facts, hours, links) is untouched.

| row | town | was | now | moved |
|---|---|---|---|---|
| Августовский канал: шлюз Домбровка | Домбровка | 53.6286, 23.7236 | 53.86292, 23.62307 | 26.9 km |
| Августовский канал: шлюз Нёмново | Нёмново | 53.682, 23.652 | 53.86973, 23.75728 | 22.0 km |
| Большая синагога (Слоним) | Слоним | 53.465, 25.23 | 53.09358, 25.31766 | 41.7 km |
| Борисоглебская церковь (Новогрудок) | Новогрудок | 53.5867, 25.8453 | 53.60016, 25.824 | 2.1 km |
| Борунский базилианский монастырь | Боруны | 54.31, 26.25 | 54.31711, 26.13995 | 7.2 km |
| Волковысское городище (Замковая гора) | Волковыск | 53.1448, 24.4727 | 53.15316, 24.47694 | 1.0 km |
| Гольшанский замок (руины) | Гольшаны | 54.414, 26.022 | 54.25149, 26.02027 | 18.1 km |
| Дворец Огинских (Слоним) | Слоним | 53.458, 25.223 | 53.09271, 25.31932 | 41.1 km |
| Дворец Радзивиллов (Дятлово) | Дятлово | 53.505, 25.288 | 53.46202, 25.39669 | 8.6 km |
| Дом-музей Адама Мицкевича (Новогрудок) | Новогрудок | 53.598, 25.833 | 53.59808, 25.82783 | 0.3 km |
| Костёл Иоанна Крестителя (Василишки) | Василишки | 53.547, 24.742 | 53.77725, 24.84943 | 26.6 km |
| Костёл Непорочного Зачатия Девы Марии (Слоним) | Слоним | 53.464, 25.2285 | 53.09472, 25.3184 | 41.5 km |
| Костёл Пресвятой Девы Марии (Большая Берестовица) | Большая Берестовица | 53.101, 24.205 | 53.19277, 24.01945 | 16.0 km |
| Костёл Пресвятой Троицы (Вороново) | Вороново | 54.133, 25.066 | 54.15205, 25.31967 | 16.7 km |
| Костёл Пресвятой Троицы (Гервяты) | Гервяты | 54.352, 26.015 | 54.6872, 26.14407 | 38.2 km |
| Костёл Пресвятой Троицы (Зельва) | Зельва | 53.213, 24.854 | 53.15014, 24.80646 | 7.7 km |
| Костёл Пресвятой Троицы (Свислочь) | Свислочь | 53.511, 24.345 | 53.0408, 24.08654 | 55.0 km |
| Костёл Святого Андрея (Солы) | Солы | 54.283, 26.417 | 54.51611, 26.18018 | 30.1 km |
| Костёл Святого Иоанна Крестителя (Гольшаны) | Гольшаны | 54.409, 26.016 | 54.25687, 26.00996 | 16.9 km |
| Костёл Святого Михаила Архангела (Михалишки) | Михалишки | 54.433, 25.967 | 54.81406, 26.16441 | 44.2 km |
| Костёл Святого Михаила Архангела (Ошмяны) | Ошмяны | 54.266, 26.313 | 54.42336, 25.93555 | 30.1 km |
| Костёл Святого Михаила Архангела (Сморгонь) | Сморгонь | 54.3115, 26.3995 | 54.48087, 26.40375 | 18.8 km |
| Костёл Святых Апостолов Петра и Павла (Ивье) | Ивье | 53.715, 25.522 | 53.93255, 25.78078 | 29.6 km |
| Костёл Святых Козьмы и Дамиана (Островец) | Островец | 54.283, 26.072 | 54.61551, 25.95421 | 37.7 km |
| Костёл Успения Пресвятой Девы Марии (Дятлово) | Дятлово | 53.503, 25.292 | 53.4653, 25.40523 | 8.6 km |
| Кревский замок (руины) | Крево | 54.617, 26.033 | 54.30929, 26.2825 | 37.8 km |
| Курган Мицкевича (Новогрудок) | Новогрудок | 53.5902, 25.8377 | 53.60232, 25.82621 | 1.5 km |
| Лидский замок | Лида | 53.8845, 25.2925 | 53.88721, 25.30284 | 0.7 km |
| Мечеть (Ивье) | Ивье | 53.714, 25.51 | 53.92223, 25.77294 | 28.9 km |
| Новогрудский замок (руины) | Новогрудок | 53.6013, 25.8278 | 53.60134, 25.82774 | 0.0 km |
| Памятник сморгонским медведям | Сморгонь | 54.31, 26.393 | 54.4798, 26.40462 | 18.9 km |
| Сморгонская синагога | Сморгонь | 54.3107, 26.3823 | 54.48178, 26.40128 | 19.1 km |
| Сынковичская церковь | Сынковичи | 53.713, 24.762 | 53.123, 25.15763 | 70.6 km |
| Фарный костёл Воздвижения Святого Креста (Лида) | Лида | 53.8833, 25.2861 | 53.88929, 25.30327 | 1.3 km |
| Фарный костёл Преображения Господня (Новогрудок) | Новогрудок | 53.5959, 25.8337 | 53.60133, 25.83008 | 0.6 km |
| Церковь Святых Апостолов Петра и Павла (Сопоцкин) | Сопоцкин | 53.831234, 23.655956 | 53.82875, 23.65737 | 0.3 km |

One more cell in the file changed and it is **not** this audit's: the `district` of
`Любчанский замок` went from Кореличский район to Новогрудский район, which is the
districts work (the column is the polygon the point falls in, and that point did not
move).

## What the correction rests on, per row

- **Confirmed by the in-dataset OSM rows** (a point within 150 m, sharing the object's
  distinctive words): the Novahrudak cluster (замок, Фарный, Борисоглебская, Мицкевич,
  курган), Гольшаны (both rows), Дятлово (both), Лида (both), Ивье (both), Сморгонь
  church, Зельва, Свислочь, Островец, Гервяты, Ошмяны, Солы, Большая Берестовица,
  Василишки, Шлюз Нёмново, Волковысское городище. For these the new point is where
  our own automated row already stood.
- **Confirmed by the extract**: `Шлюз Домбровка` — the extract carries
  `node/4228715970` «Шлюз Домбровка» exactly at the new point (0 m), which is also the
  node Valhalla's own bicycle route follows. The canal rows are the pair whose points
  matter most on the map (a bicycle route hangs off them).
- **Every corrected point is inside the raion its own row declares** — the check above,
  now gating.

## What nobody has checked — the rows that want a human

Twelve of the corrected rows have no object of their own name in our OSM rows, no
agreement from the in-dataset double test, and rest on the extract or on the town's
centre:

- **Placed on the town's centre, deliberately and visibly** — the object's own position
  is *not* verified: `Дворец Огинских (Слоним)`, `Большая синагога (Слоним)`,
  `Костёл Непорочного Зачатия Девы Марии (Слоним)`, `Сморгонская синагога`,
  `Костёл Пресвятой Троицы (Вороново)`. Slonim's two are the pair the fake 95-minute
  leg came from; they are now 300 m apart, which is the town-centre truth and not
  necessarily the building's.
- **Placed from the extract, not re-checked since**: `Сынковичская церковь`,
  `Борунский базилианский монастырь`, `Костёл Святого Михаила Архангела (Михалишки)`,
  `Костёл Святого Иоанна Крестителя (Василишки)`, `Мечеть (Ивье)`, `Памятник
  сморгонским медведям`, `Костёл Святого Андрея (Солы)`.

And the nine rows this audit did not touch at all — their points are what they were, and
nobody has verified them either: `Волковысский краеведческий музей «Замчак»`,
`Костёл Иоанна Крестителя (Мосты)`, `Костёл Пресвятой Троицы (Щучин)`, `Костёл
Святого Михаила Архангела (Волковыск)`, `Костёл Святого Николая (Мир)`, `Кальвинистский
сбор (Радунь)`, `Лютеранская кирха (Лида)`, `Синагога (Лида)`, `Озеро Свитязь`. A
point that happens to be right is not the same as a checked point, and this document
does not claim otherwise.

## Two consequences of the move

1. **One more merge pair.** `region:lida-castle` now stands on the castle, so it
   shadows `osm:relation/5755161` — the pair the pinned set in
   `tests/test_seed_merge.py::test_the_committed_files_merge_exactly_these_pairs` now
   carries. That is the rule working: a hand-authored row within 150 m of the automatic
   one of the same name takes over, and the OSM identity moves to it in `place_sources`
   (verified in the projection: `openstreetmap relation/5755161 → place 19,
   region:lida-castle`).
2. **The English name of that relation is not applied.** `place_aliases_en.json` carries
   `osm:relation/5755161 → «Lida castle»`, and the alias stage keys on the projected
   row's own `source_url` (`region:lida-castle`), so the alias finds no row and is
   dropped. This is not new and not specific to Lida: `osm:relation/1579104 → «Mir
   Castle»` has been in the same situation for as long as `region:mir-castle` has
   shadowed it. A fix belongs to the alias stage (resolve through `place_sources`
   rather than through `places.source_url`), not to this file.
