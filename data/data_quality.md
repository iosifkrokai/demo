# Data Quality

## Problem

`Qwen 2.5 1.5B Instruct` (q4_k_m, CPU) — the local LLM chosen for offline
query parsing and category classification — is too weak for the latter task.
On the initial run it labelled **33 of 76 (43%) Grodno places** as `другое`,
because the 11-category taxonomy is ambiguous to a small model on historical
Russian names like "Бывшее здание доминиканской школы".

## Solution: hand-curated ground truth

All 76 places are now manually classified, renamed, and given a one-sentence
"why visit" blurb. Source of truth: [`places_curated.csv`](places_curated.csv)
(format: `id|normalized_name|category|blurb`).

Categories (taxonomy v2 — superset of agent/llm.py + finer-grained buckets
that the small LLM could not reliably tell apart):

| Category | Count | What it covers |
|---|---|---|
| архитектура | 28 | Historic buildings: former houses, schools, factories, banks, courts |
| дворец | 7 | Palaces, residences |
| церковь | 6 | Orthodox churches |
| костёл | 6 | Catholic churches, chapels, каплицы |
| музей | 5 | Museums, galleries |
| храм | 5 | Synagogues, кирхи, non-Catholic/non-Orthodox cult places |
| монастырь | 4 | Monasteries |
| памятник | 4 | Monuments, memorial signs, graves |
| инфраструктура | 3 | Bridges, water towers, stadiums |
| кладбище | 2 | Cemeteries, necropolises |
| замок | 2 | Castles, fortifications |
| парк | 2 | Parks, squares, zoo |
| усадьба | 2 | Country estates |
| **другое** | **0** | reserved fallback |

Result: 0% `другое`, vs. 43% before.

## What the agent gets out of it

1. **`places.category`** — a precise label the LLM-extracted query
   categories (`замок`, `костёл`, ...) cleanly map to via
   `agent/search.py::CATEGORY_TO_DB`. A user asking for "архитектура" no
   longer falls through to the empty `другое` bucket.
2. **`places.name`** — cleaned of redundant "в Гродно" suffix and bare
   "бывш." prefix where the building is better known by its current function
   (e.g. "Дом офицеров" instead of "Бывший Дом офицеров в Гродно").
3. **`places.blurb`** — new column, one-sentence "why visit" used by the
   route explanation step (planned for v2 agent).
4. **Embeddings** — recomputed against `name + description + blurb` so the
   vector search picks up the new taxonomical signal.

## When to re-curate

Re-run `data/places_curated.csv` review when:
* `scripts/parse_places.py` adds new rows (no automation — hand-label them).
* `places.category` falls back to `другое` for known real places.
* A new taxonomy bucket is introduced.

## How to apply after editing the CSV

```bash
# 1. regenerate SQL
python3 -c "
import sys; sys.path.insert(0, '.')
# (use scripts/apply_curated.py; see git history)
"
# 2. apply via docker
docker cp /tmp/apply_curated.sql grodno-db:/tmp/
docker exec grodno-db psql -U grodno -d grodno -f /tmp/apply_curated.sql
```

## Why not use a bigger model?

The 1.5B Qwen is intentionally small — it ships with the agent, runs on
CPU, takes ~1 GB on disk, and answers in ~2s. A 7B Mistral or 12B model
would improve classification accuracy but:
* adds 4–8 GB RAM/CPU pressure,
* slows every `/routes/generate` call by 5–10x,
* still misclassifies ~10–20% of historical names without hand validation.

For 76 rows, hand-curation is faster, cheaper, and more correct.
