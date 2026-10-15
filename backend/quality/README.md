# backend/quality — one place for "is it still good?"

Four layers, four different questions:

| Layer | Question | Cases | Artifact |
|---|---|---|---|
| **routes** (geometry) | is our walk close to a reference walk? | `cases/routes/*.json` | `reports/report.json` or `reports/snapshots/*/report.json` |
| **curated** (geometry) | is our walk close to the walk we sell? | `cases/curated/*.json` | `reports/curated/report.json` |
| **compliance** | does the plan meet the conditions the request states? | `cases/compliance/*.json` | `reports/compliance.json` |
| **trajectory** | which tools did the agent call, and with what arguments? | `cases/trajectory.jsonl` | `reports/trajectory.json` |
| **evals** (flow stages) | which stage is to blame when the answer is bad? | `cases/*.jsonl` | `reports/evals_last.json` |

The two geometry layers ask the same question of two different sets of reference
walks, and each report names its own set (`cases_dir`), so neither can be filed
under the other's name: a `--curated` run writes to `reports/curated/`, and a
replay follows the cases it replays rather than the flag.

The sets are deliberately **small and representative** — the highest-signal cases
only. A case with no recorded run is padding, so it was dropped.

## Run

```bash
cd backend
.venv/bin/python -m quality               # geometry: live run (needs the stack)
.venv/bin/python -m quality --replay DIR  # recompute from a recorded snapshot (offline)
.venv/bin/python -m quality --golden      # compliance: live (needs the stack)
.venv/bin/python -m quality --curated     # the ready-made walks: live (needs the stack)
.venv/bin/python -m quality.trajectory    # trajectory: scripted run (needs db + Valhalla)
.venv/bin/python -m quality.evals         # the flow stages (offline ones run without a stack)
.venv/bin/python -m quality.report        # the one-page readout over all four
```

Nothing generated is committed. `python -m quality.report` reads whatever the runs
wrote under `quality/reports/` and prints **«НЕ ИЗМЕРЯЛОСЬ»** for a layer whose
artifact is missing — it never shows an unmeasured layer as green, and it exits
non-zero when *no* layer has numbers.

## Cases

* **routes** (`cases/routes/<id>.json`, `id == file stem`): one reference walk each —
  stops with coordinates, a budget, plus `case_meta`/`refinement`/`adversarial`
  blocks for the `.refine.` cases and the negative (`adv_*`) cases. Scored on recall
  (≤750 m), pool recall, order (`kendall_tau`), detour, budget fit and leg sanity.
* **compliance** (`cases/compliance/<id>.json`): a request expressed as filters
  (categories, prohibitions, region, budget, RU/EN parity) and the conditions any
  acceptable answer must meet. The loader (`quality/runner.py`) refuses unknown
  keys, so a case cannot carry an expectation nothing checks. Field reference below.
  The ask box's starter chips are these requests (and the ready-made walks'
  `query_ru`) taken word for word, so what the demo offers is what the set measures:
  `tests/test_hint_contract.py` fails the moment a chip is reworded, dropped or
  mistyped, and a request that no chip offers is a failure too — a request that names
  a moment being the one exception, since a chip cannot carry an hour. One consequence
  worth knowing: a chip is clicked with no filters at all, while a case may state
  a budget or an `avoid` its own words already imply — the chip's text is the
  promise, the filters are how the case pins it.
* **curated** (`cases/curated/<id>.json`, `id ==` an itinerary in
  `db/data/itineraries.json`): the ready-made walks scored as reference walks, run
  with `--curated`. The sidecar holds only what the data does not — the required
  `id`, `query_ru` and `budget_minutes`, and optionally `est_walk_minutes` (the
  itinerary's own travel time in minutes, which is what `Δwalk` is measured
  against) and a one-line `note` saying what the walk demonstrates. The loader is as
  strict as the compliance one: an unknown key, an `id` that is not the file stem, a
  budget outside `1..MAX_BUDGET_MIN` or a blank query is refused, so a field nothing
  reads cannot sit in the file looking like it did something. The stops are read from
  the itinerary and resolved against the datasets the seed projects, so a walk and
  the case measuring it cannot drift apart, and the `profile` comes from the
  itinerary's own `transport` rather than from the sidecar. A sidecar naming an
  itinerary that does not exist, or a stop no dataset holds, is a loud error rather
  than an empty layer. `tests/test_itineraries_contract.py` catches both offline.
* **trajectory** (`cases/trajectory.jsonl`): a scripted model turn-by-turn, and the
  tool calls the CODE actually made. A `FunctionModel` stands in for the provider
  (the same trick `tests/conftest.py` uses), so the run is offline of any provider
  and exact; the harness records each tool function the loop invoked, in order,
  with its arguments and its answer, and checks every expectation by code — there
  is no model and no judge. Needs the database and Valhalla (the tools read places
  and route legs); when either is unreachable the layer writes no artifact and stays
  «НЕ ИЗМЕРЯЛОСЬ».
* **evals** (`cases/<stage>.jsonl`): `verdicts` (offline verifier), `services`
  (`services_along.jsonl`; PostGIS, needs the DB), `interpretation` (live model),
  `plan` (live app). Every line carries a `why`; a malformed line is a loud error,
  never a silent skip; `known_gap` is visible but neither counted nor gating.

### Compliance case: field reference

```jsonc
{
  "id": "family_walk_ru",          // required — must equal the file stem
  "locale": "ru",                  // required — "ru" | "en"; the query's language
  "query": "старый Гродно, ...",   // required — 3..500 chars, written in the locale's script
  "parity_group": "family_walk",   // optional — the same request in the other locale
  "filters": {                     // required — explicit UI filters, 1:1 with GenerateReq
    "party_children": 2,           //   int 0..20 | null (a COUNT; ages are never invented)
    "hard_services": ["туалет"],   //   category codes the route MUST serve
    "interests": ["кафе"],         //   category codes the tourist wants more of (soft)
    "avoid": ["храм"],             //   category codes to keep out
    "time_budget_minutes": 120,    //   int 0..MAX_BUDGET_MIN | null
    "origin": {"lat": 53.6778, "lon": 23.8295},  // {lat, lon} | null
    "result_mode": "route",        //   "route" | "catalogue"
    "profile": "pedestrian",       // optional — "pedestrian" | "bicycle" | "auto" | …
    "at": "2026-10-20T11:00"       // optional — the moment the walk is planned for
  },
  "expectations": {                // required — machine-checkable conditions
    "must_contain_categories": ["туалет"],      // each must appear in the plan
    "must_not_contain_categories": [],          // none may appear
    "must_contain_names": ["Фарный"],           // optional — substring of a stop name
    "must_satisfy_requirement_kinds": ["free"], // optional — condition kinds, not codes
    "requirement_status": {"open": "satisfied"}, // optional — exact statuses, kind by kind
    "expected_status": ["ready", "infeasible"], // required — non-empty
    "expected_result_mode": "catalogue",        // optional
    "max_total_minutes": 120,                   // int > 0 | null (null = no cap)
    "in_region": true,                          // required — every point inside Grodno ADM1
    "allow_empty": false,                       // optional — may the plan be empty?
    "min_places": 3,                            // optional — floor on stop count
    "status_note": "why this status set"        // optional — documentation
  }
}
```

Every category value — in `filters` and in `expectations` — is a **canonical domain
code** from `core/constants.CATEGORIES` (loaded from `data/taxonomy.csv`), never
prose and never translated: an EN case carries the Russian code too. A code outside
the set is a schema error, because it would silently grade nothing.

`expected_status` names the wire's `OverallStatus` — `ready`, `degraded`, `pending`,
`infeasible` — and nothing else, because that is all `api/models/routes.py` emits.
`result_mode` is not a status (`catalogue` belongs to `expected_result_mode`), and
the old pipeline's `rejected`/`needs_clarification` no longer exist: a genuine
refusal arrives as `200` with `status="infeasible"`. The harness may still OBSERVE
`error` when the backend fails, but `error` is deliberately not a legal
expectation — a case can never expect a broken run.

`must_satisfy_requirement_kinds` is the one expectation that names no category: a
condition like `free` («бесплатно») or `open` has no code and no name, so its only
handle is the kind itself. The check reads the wire's own
`interpretation.requirements` and fails when a named kind is absent from the reading
(a request read as an ordinary walk) or when its status is `unmet`; `uncertain`
passes, because few places have a price written down and that is the honest answer.
A response with no interpretation leaves the check unverified rather than green.

`requirement_status` is the one expectation that demands a *proof* rather than a
verdict: `must_satisfy_requirement_kinds` passes on `uncertain`, so a condition whose
whole point is to be decided — «открытые в это время» — needs the status named, kind by
kind, and `uncertain` then fails like any other mismatch. A case that names a moment
(`filters.at`) additionally gets the `moment_not_applied` check: the response echoes the
moment it was checked against, and a run measured against another hour is a failure, not
a coincidence. A wall clock is read as Grodno time (`core/hours.parse_moment`), for the
case and for the request alike.

Cases sharing a `parity_group` are one request in RU and EN: exactly one case per
locale, identical `filters` and `expectations` (only `status_note` may differ), and
two different queries each written in its own script. At run time the group is
checked again on the responses — same derived status, same failed checks, same
mandatory-category outcome; the stop lists are deliberately not compared.

### Failure reasons (machine-readable)

| reason | means |
|---|---|
| `api_error` | the backend did not return a plan |
| `wrong_status` | the answer is the wrong kind of answer |
| `too_few_places` | fewer stops than the case requires (or an empty plan it does not allow) |
| `missing_mandatory_category` | a `must_contain_categories` code is absent |
| `forbidden_category_present` | an `avoid` / `must_not_contain_categories` code is present |
| `missing_named_place` | a `must_contain_names` token matches no stop |
| `requirement_unmet` | a `must_satisfy_requirement_kinds` kind was not read, or was left unmet |
| `requirement_status` | a `requirement_status` kind carries another status — `uncertain` included |
| `moment_not_applied` | the answer was checked against another moment than the case named |
| `out_of_region_point` | a returned point is outside Grodno ADM1 |
| `over_budget` | `max_total_minutes` exceeded |
| `result_mode_mismatch` | `expected_result_mode` not met |
| `ru_en_parity_mismatch` | the RU/EN pair disagreed |

## What no layer measures

Human taste, the mobile card layout, tile translation, regions other than Grodno,
and the real detour *minutes* to reach a point beside the line (`services` measures
the distance to the line, not a Valhalla walk). These stay unmeasured on purpose.

The new wire carries no per-leg trace (`debug` has no `trace` and no candidate
pool), so two route metrics the old pipeline fed are no longer measurable from a
response: the walked per-leg figure (only the straight-line hop between stops is
checked) and the exact unreachable-hop count (`n_unreachable_legs` stays 0, never
guessed). Both are reported as such in `report.json`'s `metric_notes`. The leg-cap
check that survives reads the engine's own bound rather than restating a walker's:
`routing/optimize.max_leg_seconds` — a quarter of the request's budget up to 5 km on
foot or `BICYCLE_LEG_KM` (20 km) by bicycle, and for anything motorised the whole
budget, or the drive's four-hour fallback when the request names none — converted at
the speed that transport is priced with. So a 155 km hop between two towns is not a
walking defect, a 300 km one is a defect on a four-hour drive but not on a full day's,
and a straight-line leg past the bound still is.

## Baseline: there is nothing to compare against

A numeric "it got better" against the **old pipeline** is no longer possible: that
planner's code is deleted and survives only in git history, so a current run has no
counterpart to diff. If that comparison is ever needed, `master` must be deployed
separately and measured there — do not invent a baseline here. What this harness
*docs* compare is one snapshot against another (`quality --compare A B`, a paired
bootstrap over the cases they share), and a recorded run against a live one.
