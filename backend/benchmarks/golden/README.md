# Golden set — requirement compliance

`golden/*.json` are **requirement cases**, not reference walks.

| | `benchmarks/routes/*.json` | `benchmarks/golden/*.json` |
|---|---|---|
| answers | "which stops, in what order" | "what did the tourist ask for, and what must be true of any answer" |
| graded by | geometry: recall@K, τ, detour, budget fit | conditions: category presence, bans, region, time cap, status, RU/EN parity |
| a good score means | the route is close to the reference walk | the request survived the pipeline |

They share no file and no metric. A route can score a perfect recall while
quietly dropping the mandatory toilet — that is the defect class this set exists
to catch, and the reason spec §9 says the earlier benchmark did not measure
request compliance at all.

The loader in `backend/scripts/bench_routes.py` **is** the schema: it refuses
unknown keys, so a case cannot carry an expectation that nothing checks. Run it
with `--golden` (live) or `--replay-golden DIR` (offline); `pytest
tests/test_bench_golden.py` proves the files parse and the scorer behaves.

## Schema

```jsonc
{
  "id": "family_walk_ru",          // required — must equal the file stem
  "locale": "ru",                  // required — "ru" | "en"; the query's language
  "query": "старый Гродно, ...",   // required — the text sent to /routes/generate
  "parity_group": "family_walk",   // optional — same request, other locale
  "filters": {                     // required — explicit UI filters, 1:1 with GenerateReq
    "party_children": 2,           //   int 0..20 | null (a COUNT; ages are never invented)
    "hard_services": ["туалет"],   //   category codes the route MUST serve
    "interests": ["кафе"],         //   category codes the tourist wants more of (soft)
    "avoid": ["храм"],             //   category codes to keep out
    "time_budget_minutes": 120,    //   int 0..480 | null
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

### Category values are codes, never prose

Every category value — in `filters` and in `expectations` — is a **canonical
domain code** from `agent/constants.CATEGORIES` (`замок`, `костёл`, `церковь`,
`монастырь`, `дворец`, `усадьба`, `парк`, `музей`, `памятник`, `храм`,
`архитектура`, `инфраструктура`, `кладбище`, `кафе`, `ресторан`, `туалет`,
`гостиница`). An EN case carries `"туалет"` too: the codes are the contract
between the UI, the planner and the data, so they are not translated. A code
outside this set is a schema error, because it would silently grade nothing.

### Statuses

`ready`, `catalogue`, `degraded`, `pending`, `infeasible`, `rejected`,
`needs_clarification`, `error`.

The API does not expose a plan status yet, so the scorer **derives** one and
records the source (`status_source`) instead of pretending the API said it:

1. a 4xx → `rejected`, a 5xx or transport failure → `error`;
2. else `response.status`, if the API ships one (it wins);
3. else `response.requirements[]`, via the planner's own ordering
   (`unmet` → `infeasible`, `uncertain` → `degraded`, `pending` → `pending`,
   otherwise `ready`);
4. else a 200 with no points → `infeasible`;
5. else a 200 with points → `ready`.

`expected_status` is therefore the set of **honest outcomes**, and it always
lists more than one where the API can express more than one. It is not widened
to make a case pass: when the only true answer is one the API cannot express
yet, the case notes it in `status_note`.

### Parity groups

Cases sharing a `parity_group` are the same request in RU and EN. The loader
enforces that mechanically:

* exactly one case per locale (`ru` and `en`);
* identical `filters` and identical `expectations` (only `status_note` may
  differ, because it is documentation);
* the two queries differ, and each is written in its own locale (an `"en"` case
  may not contain Cyrillic, a `"ru"` case must).

At run time the group is checked again on the **responses**: RU and EN must get
the same derived status, the same set of failed checks and the same
mandatory-category outcome. The stop lists are deliberately *not* compared —
the two runs pick different stops, and demanding identical routes would make the
check noise instead of a contract.

## Failure reasons (machine-readable)

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

## The cases

| case | locale | what it pins |
|---|---|---|
| `family_walk_ru` / `family_walk_en` | ru/en | spec §9.1 acceptance: children count, mandatory toilet, soft café, 2 h — in both locales |
| `hard_toilet_soft_cafe_en` | en | hard vs soft: the toilet is mandatory, the café is not |
| `museums_budget_ru` / `museums_budget_en` | ru/en | a themed request with an explicit budget, in both locales |
| `named_farnyi` | ru | a named stop must be on the route |
| `named_mir_castle` | ru | the same, outside Grodno city |
| `avoid_cafe_want_parks` | ru | a ban (`avoid`) plus a want |
| `avoid_temples` | ru | a family walk with four banned categories and a required park |
| `castles_half_day` | ru | a region-wide theme on a half-day budget |
| `all_churches_catalogue` | ru | an exhaustive region request that must be a catalogue, not a short walk |
| `out_of_region_vilnius` | ru | an out-of-region request must not produce an out-of-region route |
| `vague_no_anchor` | ru | no anchor: clarify, or answer inside the region |
