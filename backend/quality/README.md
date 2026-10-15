# backend/quality — one place for "is it still good?"

Four layers, four different questions:

| Layer | Question | Cases | Artifact |
|---|---|---|---|
| **routes** (geometry) | is our walk close to a reference walk? | `cases/routes/*.json` | `reports/snapshots/*/report.json` |
| **compliance** | does the plan meet the conditions the request states? | `cases/compliance/*.json` | `reports/compliance.json` |
| **trajectory** | which tools did the agent call, and with what arguments? | `cases/trajectory.jsonl` | `reports/trajectory.json` |
| **evals** (flow stages) | which stage is to blame when the answer is bad? | `cases/*.jsonl` | `reports/evals_last.json` |

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
* **curated** (`cases/curated/<id>.json`, `id ==` an itinerary in
  `db/data/itineraries.json`): the ready-made walks scored as reference walks, run
  with `--curated`. The sidecar holds only what the data does not — `query_ru` and
  `budget_minutes`; the stops are read from the itinerary and resolved against the
  datasets the seed projects, so a walk and the case measuring it cannot drift
  apart. A sidecar naming an itinerary that does not exist, or a stop no dataset
  holds, is a loud error rather than an empty layer.
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
    "result_mode": "route"         //   "route" | "catalogue"
  },
  "expectations": {                // required — machine-checkable conditions
    "must_contain_categories": ["туалет"],      // each must appear in the plan
    "must_not_contain_categories": [],          // none may appear
    "must_contain_names": ["Фарный"],           // optional — substring of a stop name
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
guessed). Both are reported as such in `report.json`'s `metric_notes`.

## Baseline: there is nothing to compare against

A numeric "it got better" against the **old pipeline** is no longer possible: that
planner's code is deleted and survives only in git history, so a current run has no
counterpart to diff. If that comparison is ever needed, `master` must be deployed
separately and measured there — do not invent a baseline here. What this harness
*docs* compare is one snapshot against another (`quality --compare A B`, a paired
bootstrap over the cases they share), and a recorded run against a live one.
