# Golden-set compliance report

**Mode:** `golden-live` · **Generated:** 2026-09-29T06:12:50.277728+00:00 · **API:** `http://localhost:8080`

This measures whether the CONDITIONS of a request survived, not how close the route came to a reference walk. A case passes only if every machine-checkable expectation of its file holds; a parity group passes only if RU and EN got the same kind of answer.

**Compliance rate: 1.000** (15/15 units)

| case | locale | verdict | status | reason | detail |
|---|---|---|---|---|---|
| all_churches_catalogue | — | PASS | ready | ok | 1/1 repeat(s) passed |
| avoid_cafe_want_parks | — | PASS | ready | ok | 1/1 repeat(s) passed |
| avoid_temples | — | PASS | ready | ok | 1/1 repeat(s) passed |
| castles_half_day | — | PASS | ready | ok | 1/1 repeat(s) passed |
| family_walk_en | — | PASS | ready | ok | 1/1 repeat(s) passed |
| family_walk_ru | — | PASS | ready | ok | 1/1 repeat(s) passed |
| hard_toilet_soft_cafe_en | — | PASS | ready | ok | 1/1 repeat(s) passed |
| museums_budget_en | — | PASS | ready | ok | 1/1 repeat(s) passed |
| museums_budget_ru | — | PASS | ready | ok | 1/1 repeat(s) passed |
| named_farnyi | — | PASS | ready | ok | 1/1 repeat(s) passed |
| named_mir_castle | — | PASS | ready | ok | 1/1 repeat(s) passed |
| out_of_region_vilnius | — | PASS | infeasible | ok | 1/1 repeat(s) passed |
| vague_no_anchor | — | PASS | ready | ok | 1/1 repeat(s) passed |
| parity:family_walk | ru/en | PASS | ready | ok | en: status=ready, failed=none, mandatory=туалет=present; ru: status=ready, failed=none, mandatory=туалет=present |
| parity:museums_budget | ru/en | PASS | ready | ok | en: status=ready, failed=none, mandatory=музей=present; ru: status=ready, failed=none, mandatory=музей=present |
