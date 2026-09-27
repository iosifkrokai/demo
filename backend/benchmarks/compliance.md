# Golden-set compliance report

**Mode:** `golden-live` · **Generated:** 2026-09-27T14:01:21.875022+00:00 · **API:** `http://localhost:8080`

This measures whether the CONDITIONS of a request survived, not how close the route came to a reference walk. A case passes only if every machine-checkable expectation of its file holds; a parity group passes only if RU and EN got the same kind of answer.

**Compliance rate: 0.667** (10/15 units)

| case | locale | verdict | status | reason | detail |
|---|---|---|---|---|---|
| all_churches_catalogue | — | FAIL | degraded | wrong_status | 0/1 repeat(s) passed; repeat failed with wrong_status: status degraded (response.status) is not one of ['ready', 'catalogue'] |
| avoid_cafe_want_parks | — | PASS | ready | ok | 1/1 repeat(s) passed |
| avoid_temples | — | PASS | ready | ok | 1/1 repeat(s) passed |
| castles_half_day | — | PASS | ready | ok | 1/1 repeat(s) passed |
| family_walk_en | — | FAIL | ready | missing_mandatory_category | 0/1 repeat(s) passed; repeat failed with missing_mandatory_category: missing mandatory category(ies) ['туалет']; the plan has ['костёл', 'музей', 'памятник'] |
| family_walk_ru | — | FAIL | ready | missing_mandatory_category | 0/1 repeat(s) passed; repeat failed with missing_mandatory_category: missing mandatory category(ies) ['туалет']; the plan has ['костёл', 'музей', 'памятник'] |
| hard_toilet_soft_cafe_en | — | FAIL | ready | missing_mandatory_category | 0/1 repeat(s) passed; repeat failed with missing_mandatory_category: missing mandatory category(ies) ['туалет']; the plan has ['костёл', 'музей', 'памятник'] |
| museums_budget_en | — | PASS | ready | ok | 1/1 repeat(s) passed |
| museums_budget_ru | — | PASS | ready | ok | 1/1 repeat(s) passed |
| named_farnyi | — | PASS | ready | ok | 1/1 repeat(s) passed |
| named_mir_castle | — | PASS | ready | ok | 1/1 repeat(s) passed |
| out_of_region_vilnius | — | FAIL | ready | wrong_status | 0/1 repeat(s) passed; repeat failed with wrong_status: status ready (response.status) is not one of ['rejected', 'infeasible', 'needs_clarification'] |
| vague_no_anchor | — | PASS | ready | ok | 1/1 repeat(s) passed |
| parity:family_walk | ru/en | PASS | ready | ok | en: status=ready, failed=missing_mandatory_category, mandatory=туалет=MISSING; ru: status=ready, failed=missing_mandatory_category, mandatory=туалет=MISSING |
| parity:museums_budget | ru/en | PASS | ready | ok | en: status=ready, failed=none, mandatory=музей=present; ru: status=ready, failed=none, mandatory=музей=present |

## Failures by reason

- `missing_mandatory_category`: 3
- `wrong_status`: 2

## Checks the current API cannot answer

These are recorded as unverified rather than passed or failed, because the response carries no field to decide them:

- `all_churches_catalogue:result_mode_mismatch`
