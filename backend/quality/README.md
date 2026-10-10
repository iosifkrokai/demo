# backend/quality — one place for "is it still good?"

Three layers, three different questions:

| Layer | Question | Cases | Artifact |
|---|---|---|---|
| **routes** (geometry) | is our walk close to a reference walk? | `cases/routes/*.json` | `reports/snapshots/*/report.json` |
| **compliance** | did the request survive the pipeline? | `cases/compliance/*.json` | `reports/compliance.json` |
| **evals** (flow stages) | which stage is to blame when the answer is bad? | `cases/*.jsonl` | `reports/evals_last.json` |

The sets are deliberately **small and representative** — the highest-signal cases
only. A case with no recorded run is padding, so it was dropped.

## Run

```bash
cd backend
.venv/bin/python -m quality               # geometry: live run (needs the stack)
.venv/bin/python -m quality --replay DIR  # recompute from a recorded snapshot (offline)
.venv/bin/python -m quality --golden      # compliance: live (needs the stack)
.venv/bin/python -m quality.evals         # the flow stages (offline ones run without a stack)
.venv/bin/python -m quality.report        # the one-page readout over all three
```

Nothing generated is committed. `python -m quality.report` reads whatever the runs
wrote under `quality/reports/` and prints **«НЕ ИЗМЕРЯЛОСЬ»** for a layer whose
artifact is missing — it never shows an unmeasured layer as green, and it exits
non-zero when *no* layer has numbers.

## Cases

* **routes** (`cases/routes/<id>.json`, `id == file stem`): one reference walk each —
  stops with coordinates, a budget, an optional `case_meta.kind`, plus a
  `refinement` block for `.refine.` cases and an `adversarial` block for the three
  negative cases. Scored on recall (≤750 m), pool recall, order (`kendall_tau`),
  detour, budget fit and leg sanity.
* **compliance** (`cases/compliance/<id>.json`): a request expressed as filters
  (categories, prohibitions, region, budget, RU/EN parity) and the conditions any
  acceptable answer must meet. The loader (`quality/runner.py`) refuses unknown
  keys, so a case cannot carry an expectation nothing checks. Field reference below.
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
code** from `domain/constants.CATEGORIES` (loaded from `data/taxonomy.csv`), never
prose and never translated: an EN case carries the Russian code too. A code outside
the set is a schema error, because it would silently grade nothing. Statuses:
`ready`, `catalogue`, `degraded`, `pending`, `infeasible`, `rejected`,
`needs_clarification`, `error`.

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
