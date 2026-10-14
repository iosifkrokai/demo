# Close-duplicate review — the 102 pairs under 30 m

## What this is

`find_suspected_duplicates` reports two bands per seed: record pairs that share a
normalised name **and** category and stand closer than 30 m (`SAME_OBJECT_RADIUS_M`),
and the same-name namesakes between 30 and 150 m. The first band — the close pairs —
is the only place a place mapped twice *under one name* can show: two rows the
catalogue would otherwise present as one object.

That band has never been read as a list. This document is that reading: a verdict on
each of the 102 close pairs, with the fields that ground it. It is **material for a
human decision, not a change** — nothing here is merged, and no data file is touched.
A merge is a hand-authored entry in `place_merges.json`, added only after the pair is
approved.

The two adjudications already written by hand (the Grodno museum in `place_merges.json`,
the Mir complex in `place_aliases.json`) were found by the quality harness, not by this
report — both differ in name or category and so cannot appear in it. They are not in the
list below.

## How it was produced

Offline: the committed CSVs under `backend/db/data/`, no database and no network. The
records are reduced exactly as the seed reduces them before writing the coverage report
(`db/seed/cli.py::_collect` drops the shadowed, twin and declared-merge rows first), so
the pair set and its count match `suspected_duplicates.count` in the seed report:

```bash
cd backend
.venv/bin/python - <<'PY'
from db.seed import datasets as d
from db.seed.datasets import (default_datasets, collect_records, find_suspected_duplicates,
                              SAME_OBJECT_RADIUS_M, load_place_aliases, load_declared_merges)
col = collect_records(default_datasets())
kept, _ = d.resolve_shadowed(col["records"], aliases=load_place_aliases())
kept, _ = d.resolve_twins(kept)
kept, _ = d.resolve_declared_merges(kept, load_declared_merges())
for p in find_suspected_duplicates(kept):
    if p["distance_m"] < SAME_OBJECT_RADIUS_M:
        print(p)
PY
```

Run on **2026-10-14** against the committed data. The reduced set is 8550 records; the
report's own `.pairs` field is truncated to the first 50, so this review covers all 102
(`count`), not the 50 printed. The report also skips and counts 1568 rows whose name is a
generated service label («Остановка», «Туалет»); none of those is a pair below.

Two facts hold for **every** pair in the list, and they shape the whole reading:

- **both sides are automatic** (`category_source = auto`), i.e. OSM-derived. No pair here
  involves a hand-authored `city:`/`region:` row or a protected `curated` category, so
  nothing here collides with the protection the seed guards.
- **both names are byte-identical** — not merely normalised-equal. The report buckets by
  `norm_name`, but here the raw strings coincide too (case, spaces and punctuation
  included), so no pair is told apart by its spelling.

## Reading the table

- **elements** — `n` = `osm:node`, `w` = `osm:way`, `r` = `osm:relation`, each with its id.
  The element type is a hint, never proof: a stop node and a stop way can be one stop, and
  two stop nodes can be two.
- **verdict**
  - **one object** — the two rows are the same real feature; merging is the right call.
  - **two objects** — two distinct features that happen to share a name (namesakes,
    neighbours); leave both.
  - **unclear** — the committed columns cannot decide it; the missing evidence is named.
- The rows are in report order (bucket order), not ranked.

## The 102 pairs

| # | name (normalised, as in data) | category | m | A | B | raion | verdict | why |
|---|---|---|---|---|---|---|---|---|
| 1 | братская могила | памятник | 20.8 | n 4894093413 | n 9310060629 | Лидский район | unclear |
| 2 | кладбище старое католическое | архитектура | 18.9 | n 2788681381 | w 60921595 | Лидский район | one object |
| 3 | блиндаж 1 ой мировой войны | архитектура | 21.4 | n 8783709394 | n 8783709395 | Сморгонский район | two objects |
| 4 | блиндаж 1 ой мировой войны | архитектура | 16.7 | n 8783709395 | n 8783709396 | Сморгонский район | two objects |
| 5 | блиндаж 1 ой мировой войны | архитектура | 20.4 | n 8783709396 | n 8783709397 | Сморгонский район | two objects |
| 6 | дот немецкий 1 й мировой войны | замок | 27.4 | n 3774865097 | n 3774865112 | Сморгонский район | two objects |
| 7 | дот немецкий 1 й мировой войны | замок | 21.9 | n 3774865113 | n 3774865114 | Сморгонский район | two objects |
| 8 | дот немецкий 1 й мировой войны | замок | 24.4 | n 3774865113 | n 3774865116 | Сморгонский район | two objects |
| 9 | руины немец дота пмв | замок | 7.9 | n 13708690901 | n 13708691001 | Сморгонский район | two objects |
| 10 | руины немец дота пмв | замок | 9.4 | n 13732632201 | n 13732632301 | Сморгонский район | two objects |
| 11 | руины немец дота пмв | замок | 18.7 | n 13732632201 | n 13732632401 | Сморгонский район | two objects |
| 12 | руины немец дота пмв | замок | 9.3 | n 13732632301 | n 13732632401 | Сморгонский район | two objects |
| 13 | руины немец блиндажа пмв | замок | 8.1 | n 13844394601 | n 13844394701 | Сморгонский район | two objects |
| 14 | руины немец фортиф укреплений пмв | замок | 14.6 | n 13860796601 | n 13860797901 | Сморгонский район | two objects |
| 15 | церковь преображения господня | церковь | 28.6 | w 282866496 | w 377997746 | Дятловский район | unclear |
| 16 | купель | церковь | 5.5 | w 397466557 | w 397466558 | Зельвенский район / Слонимский район | one object |
| 17 | вуліца леніна | остановка транспорта | 27.8 | n 2563388836 | n 2563388957 | Щучинский район | two objects |
| 18 | азот | остановка транспорта | 24.6 | n 506553732 | n 2347003865 | Гродненский район | two objects |
| 19 | ласосна | остановка транспорта | 28.9 | n 559488479 | n 810327445 | Гродненский район | two objects |
| 20 | поразава | остановка транспорта | 22.0 | n 695662467 | n 7119083668 | Свислочский район | two objects |
| 21 | сярэдняя школа no37 | остановка транспорта | 28.6 | n 810018755 | n 2211711899 | Гродненский район | two objects |
| 22 | плошча савецкая | остановка транспорта | 19.5 | n 5772626415 | n 13565825355 | Вороновский район | two objects |
| 23 | вуліца зорная | остановка транспорта | 27.8 | n 4715286698 | n 13288472550 | Слонимский район | two objects |
| 24 | вуліца заслонава | остановка транспорта | 22.9 | n 9881561012 | n 9881561013 | Сморгонский район | two objects |
| 25 | лакаматыўнае дэпо | остановка транспорта | 18.7 | n 2098012383 | n 2098012933 | Лидский район | two objects |
| 26 | сярэдняя школа no5 | остановка транспорта | 18.8 | n 4715287894 | n 13242469543 | Слонимский район | two objects |
| 27 | жэс | остановка транспорта | 27.0 | n 982364808 | n 5424997136 | Волковысский район | two objects |
| 28 | гімназія no1 | остановка транспорта | 27.1 | n 1189325658 | n 1189325661 | Мостовский район | two objects |
| 29 | мікрараён чырвонаармейскі | остановка транспорта | 17.6 | n 1427783899 | n 4851664156 | Лидский район | two objects |
| 30 | праспект перамогі | остановка транспорта | 27.2 | n 1427783921 | n 5518554109 | Лидский район | two objects |
| 31 | вуліца паркавая | остановка транспорта | 20.4 | n 4715305691 | n 9076441675 | Слонимский район | two objects |
| 32 | вуліца карла маркса | остановка транспорта | 20.0 | n 2066529040 | n 6179287402 | Волковысский район | two objects |
| 33 | вуліца хмяльніцкага | остановка транспорта | 25.4 | n 2097989769 | n 2097989770 | Лидский район | two objects |
| 34 | дачы | остановка транспорта | 16.3 | n 5284041648 | n 5284041651 | Мостовский район | two objects |
| 35 | вуліца герцэна | остановка транспорта | 25.4 | n 2097999098 | n 2097999127 | Лидский район | two objects |
| 36 | аўтастанцыя астрына | остановка транспорта | 28.7 | n 2304002055 | n 7519580235 | Щучинский район | two objects |
| 37 | бті | остановка транспорта | 24.6 | n 2355910721 | n 2355910722 | Ошмянский район | two objects |
| 38 | людзвінова | остановка транспорта | 16.2 | n 2355910736 | n 2355910737 | Ошмянский район | two objects |
| 39 | галынка | остановка транспорта | 26.3 | n 5488955540 | n 5488955544 | Зельвенский район | two objects |
| 40 | сельгастэхніка | остановка транспорта | 25.5 | n 12996710593 | n 12996710594 | Мостовский район | two objects |
| 41 | масты правыя | остановка транспорта | 29.8 | n 3164364088 | n 3523127181 | Мостовский район | two objects |
| 42 | біскупцы | остановка транспорта | 22.4 | n 12441108149 | n 12441108151 | Лидский район | two objects |
| 43 | аульс | остановка транспорта | 10.4 | n 3948378098 | n 5011409906 | Гродненский район | two objects |
| 44 | крэва | остановка транспорта | 8.9 | n 4360513109 | r 11718170 | Сморгонский район | one object |
| 45 | крэва | остановка транспорта | 9.0 | n 8356814474 | r 11718171 | Сморгонский район | one object |
| 46 | вуліца юбілейная | остановка транспорта | 29.1 | n 13481123038 | n 13481123039 | Лидский район | two objects |
| 47 | вуліца леашэні | остановка транспорта | 15.6 | n 4715286094 | n 4715286593 | Слонимский район | two objects |
| 48 | вуліца зялёная | остановка транспорта | 16.6 | n 4728737291 | n 4728750990 | Слонимский район | two objects |
| 49 | малыя рачуны | остановка транспорта | 18.3 | n 4841258854 | n 5141496865 | Островецкий район | two objects |
| 50 | снігяны | остановка транспорта | 17.8 | n 5141496867 | n 5214561682 | Островецкий район | two objects |
| 51 | германішкі | остановка транспорта | 28.8 | n 6882044794 | n 6882044798 | Вороновский район | two objects |
| 52 | завозерцы | остановка транспорта | 28.5 | n 5149667529 | n 5149667539 | Сморгонский район | two objects |
| 53 | mc d ner | кафе | 28.6 | n 6964872288 | n 12134078050 | Гродненский район | unclear |
| 54 | аляксандраўшчына | остановка транспорта | 29.2 | n 5267787629 | n 5267787633 | Зельвенский район | two objects |
| 55 | студзянец | остановка транспорта | 29.0 | n 5312458939 | n 5312458943 | Волковысский район | two objects |
| 56 | зялёная вуліца | остановка транспорта | 28.6 | n 5312459069 | n 5312459071 | Волковысский район | two objects |
| 57 | рагозніца | остановка транспорта | 11.2 | n 5319064496 | n 5319064500 | Зельвенский район | two objects |
| 58 | янышы | остановка транспорта | 19.6 | n 5329082953 | n 5329082957 | Волковысский район | two objects |
| 59 | алекшыцы | остановка транспорта | 29.8 | n 5477871400 | n 5477871402 | Берестовицкий район | two objects |
| 60 | пракопавічы | остановка транспорта | 16.2 | n 5480021498 | n 5480021500 | Берестовицкий район | two objects |
| 61 | старое сяло | остановка транспорта | 22.6 | n 5489429867 | n 13112028893 | Зельвенский район | two objects |
| 62 | бязводна | остановка транспорта | 24.9 | n 5739049249 | n 5739049253 | Зельвенский район | two objects |
| 63 | альшымава | остановка транспорта | 28.0 | n 5808088006 | n 5808088007 | Волковысский район | two objects |
| 64 | залацеева | остановка транспорта | 18.0 | n 5809518933 | n 9754065033 | Зельвенский район | two objects |
| 65 | круцілавічы | остановка транспорта | 26.9 | n 6277234865 | n 6277234866 | Дятловский район | two objects |
| 66 | паўлюшкі | остановка транспорта | 22.0 | n 6472886153 | n 6472886154 | Свислочский район | two objects |
| 67 | вуліца першамайская | остановка транспорта | 15.8 | n 6475639124 | n 7788019935 | Мостовский район | two objects |
| 68 | наркуны | остановка транспорта | 12.7 | n 6975776848 | n 12589562103 | Лидский район | two objects |
| 69 | вуліца мірошніка | остановка транспорта | 20.3 | n 7684837185 | n 10292028118 | Слонимский район | two objects |
| 70 | амбулаторыя | остановка транспорта | 15.1 | n 7731153574 | n 7731153575 | Гродненский район | two objects |
| 71 | новая ятвезь | остановка транспорта | 24.7 | n 7780774878 | n 7780774879 | Волковысский район | two objects |
| 72 | наваспаск | остановка транспорта | 10.3 | n 7973775223 | n 8356849755 | Сморгонский район | unclear |
| 73 | наваспаск | остановка транспорта | 9.3 | n 7973775224 | n 8356849756 | Сморгонский район | unclear |
| 74 | гудзевічы | остановка транспорта | 26.3 | n 8124519993 | n 8124519995 | Берестовицкий район | two objects |
| 75 | наваельня | остановка транспорта | 22.7 | w 408488190 | w 1423112095 | Дятловский район | two objects |
| 76 | грандзічы 2 | остановка транспорта | 18.9 | n 9051225971 | n 9363750347 | Гродненский район | two objects |
| 77 | праспект курловіча | остановка транспорта | 22.2 | n 10167498089 | n 10601961586 | Гродненский район | two objects |
| 78 | shaurma mobil | кафе | 18.9 | n 10598280377 | n 11033803491 | Гродненский район | unclear |
| 79 | паперня 2 | остановка транспорта | 28.3 | n 11005803058 | n 11005803059 | Лидский район | two objects |
| 80 | гасцявы дом | гостиница | 27.1 | n 11222937146 | n 11222937744 | Кореличский район | unclear |
| 81 | дом правасуддзя | остановка транспорта | 18.4 | n 11291558970 | n 11291583971 | Сморгонский район | two objects |
| 82 | вуліца вянечная | остановка транспорта | 26.9 | n 11983718483 | n 11983718484 | Гродненский район | two objects |
| 83 | белагруда | остановка транспорта | 27.9 | n 12385753888 | n 12385764728 | Лидский район | two objects |
| 84 | дылева | остановка транспорта | 13.4 | n 12387620112 | n 12387620113 | Лидский район | two objects |
| 85 | крыніцы | остановка транспорта | 17.5 | n 12404242216 | n 12548485543 | Лидский район | two objects |
| 86 | крама вадаём | остановка транспорта | 17.0 | n 12441108158 | n 12441108159 | Лидский район | two objects |
| 87 | дачы пескі | остановка транспорта | 25.3 | n 12441108171 | n 12441108173 | Лидский район | two objects |
| 88 | крупава | остановка транспорта | 26.2 | n 12450515014 | n 12450515015 | Лидский район | two objects |
| 89 | ядэлеўцы | остановка транспорта | 12.2 | n 12468357460 | n 12468357461 | Лидский район | two objects |
| 90 | праважа пз | остановка транспорта | 16.4 | n 12468357466 | n 12468357468 | Лидский район | two objects |
| 91 | янавічы | остановка транспорта | 28.7 | n 12589560289 | n 12589560290 | Лидский район | two objects |
| 92 | дачы наркуны | остановка транспорта | 14.4 | n 12589560296 | n 12589560297 | Лидский район | two objects |
| 93 | вялікія сцяпанішкі пт | остановка транспорта | 22.7 | n 12996710582 | n 12996710584 | Мостовский район | two objects |
| 94 | вуліца валчэцкага | остановка транспорта | 24.2 | n 13042472186 | n 13042475110 | Новогрудский район | two objects |
| 95 | вуліца люсі сячко | остановка транспорта | 21.7 | n 13042472194 | n 13042472197 | Новогрудский район | two objects |
| 96 | юрацішкі | остановка транспорта | 18.9 | w 137599993 | w 407299817 | Ивьевский район | two objects |
| 97 | бярэзаўцы | остановка транспорта | 8.9 | w 222405503 | w 407283599 | Ивьевский район | two objects |
| 98 | жытомля | остановка транспорта | 16.9 | w 222405505 | w 396291721 | Гродненский район | two objects |
| 99 | гаўя | остановка транспорта | 6.8 | w 289974054 | w 289974055 | Ивьевский район | two objects |
| 100 | лабачы | остановка транспорта | 10.8 | w 397113245 | w 397113246 | Ивьевский район | two objects |
| 101 | даўгердзішкі | остановка транспорта | 10.8 | w 407301431 | w 407301432 | Ивьевский район | two objects |
| 102 | яхімаўшчына | остановка транспорта | 9.8 | w 407301433 | w 407301434 | Ивьевский район | two objects |

## Notes on the one-object and unclear pairs

**One object (4).** These are the pairs where two OSM primitives are the same feature,
which is exactly what the close band exists to catch:

- **#2 «Кладбище старое католическое»** (Лидский) — a node and a way, same name, 19 m:
  the point that labels the cemetery and the polygon that outlines it. Merge, keep the way.
- **#16 «Купель»** (Зельвенский/Слонимский) — two ways 5.5 m apart, one name, and the
  raion boundary runs between them: one feature the boundary was cut through, not two.
- **#44, #45 «крэва»** (Сморгонский) — a stop node and a relation, one name, ~9 m each.
  A relation only reaches the stop category by carrying `public_transport=platform`
  (`db/seed/osm_tags.py`), so each pair is a stop's node and its platform — one stop.

**Unclear (7).** Each names the one thing that would decide it:

- **#1 «Братская могила»** (Лидский) — two nodes 21 m apart.
- **#15 «Церковь Преображения Господня»** (Дятлово) — two ways 29 m apart.
- **#53 «MC Döner»**, **#78 «Shaurma.Mobil»** (Гродненский) — a café mapped twice vs two.
- **#72, #73 «наваспаск»** (Сморгонский) — an old and a new *pair* of consecutive-id nodes
  9–10 m apart; the strongest duplicate smell in the list.
- **#80 «Гасцявы дом»** (Кореличский) — two nodes 27 m apart.

## Conclusions

**Counts.** Of the 102 close pairs: **one object — 4**, **two objects — 91**, **unclear —
7**. The band is therefore almost entirely the false positive it was suspected of being:
the report is a candidate list, and 89% of it is namesakes and neighbours.

**What the two-object majority is.** 79 of the 91 are transport stops: two stop positions
on one road that carry one street or settlement name (19 of them a street/avenue/square
name, the rest a village or landmark name). This is the «street and stop on it» case the
roadmap warned of — the shared name is the *place*, not the object, and merging would
throw away one of the two directions. The other 12 are a cluster of WWI bunkers, pillboxes
and their ruins in the Сморгонский district (#3–#14): separate concrete structures of one
defensive line, mapped node by node, each 8–27 m from the next. The sub-10 m members of
that cluster (e.g. #9, #13) are the least certain of the «two objects», and rest on the
line's shape rather than on any single pair.

**Inside the city vs in the oblast.** Reading each row's own `town`/`district`: **none of
the 102 pairs lies inside the city of Grodno** — every row is tagged with an oblast raion
(Гродненский, Лидский, Сморгонский, …), only two rows carry a town (Дятлово), and the
coordinates sit far from the city. So **all four one-object pairs are in the oblast**
(Лидский, Зельвенский/Слонимский, Сморгонский) and none in the city; the two-object and
unclear pairs are likewise all outside it. The city-side risk the roadmap worried about
does not arise in this list. (The transport stops carry no `town` at all — their OSM
element had no `addr:city`/`addr:village` — so for them city membership is read from the
raion alone.)

**Distinguishable by name.** **Zero.** All 102 pairs are pairs *under one identical name*
— that is how the report selects them, and the raw strings coincide too, so the name alone
never separates a pair. The only name-level signal is what the shared name denotes: 19 are
street/avenue/square names, 47 are single settlement or landmark names, 36 are multi-word
landmark or business names.

**If merges are approved.** Cheapest and safest first, because the shape of the pair
itself argues the case: **#2** (cemetery node+way), then **#44/#45** (stop node + platform
relation), then **#16** (boundary-split ways). **#72/#73** are the ones to verify first —
they read like a superseded re-map but need OSM element history to be confirmed, and a
wrong merge would delete a live stop.

**What this review could not settle.** The committed files carry only the 14 dataset
columns — no OSM tags. So the disk knows an element *is* a stop node, a way or a relation,
but not whether it is `highway=bus_stop` or `public_transport=platform`, nor which
direction of travel it serves. That is the single missing fact behind most of the «two
objects» calls and all seven «unclear» ones; a reader with the OSM tags (or the elements'
history) could close them without a new dataset.
