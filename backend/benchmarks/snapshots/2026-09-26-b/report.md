# Benchmark Report: Grodno Route Planner

**Mode:** `replay` · **Generated:** 2026-09-26T13:27:20.553080+00:00

**Runs per case:** 1 · **cases:** 3 · **runs:** 3

**Snapshot:** `/workspaces/demo/backend/benchmarks/snapshots/2026-09-26-b` · 3 row(s) · git `79de0ba`

## Per-case scores (`mean±spread` over repeats)

| case | S1 pool | S2 \| S1 | Rec@K | prec | τ | detour km | Δwalk | fit | dup | unr | HF |
|---|---|---|---|---|---|---|---|---|---|---|---|
| grodno_old_town | 0.60±0.00 | 1.00±0.00 | 1.00±0.00 | 0.83±0.00 | 0.20±0.00 | 0.811±0.000 | -9.8±0.0 | 1/1 | 1 | 0 | 0 |
| mir | 0.33±0.00 | 1.00±0.00 | 0.67±0.00 | 0.67±0.00 | n/a | -0.017±0.000 | -39.9±0.0 | 1/1 | 0 | 0 | 0 |
| novogrudok | 0.25±0.00 | 1.00±0.00 | 0.50±0.00 | 1.00±0.00 | n/a | -0.615±0.000 | -31.2±0.0 | 1/1 | 0 | 0 | 0 |

## Overall — bootstrap over cases (B=10000, seed=20260926)

| metric | mean [95 % CI] | n cases | noise floor (within-case spread) |
|---|---|---|---|
| Rec@K (750 m greedy) | 0.722 [0.500, 1.000] | 3 | n/a |
| stage-1 pool proxy recall (250 m) | 0.394 [0.250, 0.600] | 3 | n/a |
| stage-2 recall | stage-1 hit | 1.000 [1.000, 1.000] | 3 | n/a |
| precision | 0.833 [0.667, 1.000] | 3 | n/a |
| τ shared (vs reference walk) | 0.200 (n=1, no CI) | 1 | n/a |
| detour km | 0.060 [-0.615, 0.811] | 3 | n/a |
| Δwalk min | -26.9 [-39.9, -9.8] | 3 | n/a |
| max leg km (from trace) | 0.68 [0.40, 1.05] | 3 | n/a |
| latency s | 14.03 [6.85, 27.68] | 3 | n/a |

Reference noise floor: recall 0.711 ± 0.150 over 9 runs (measured 2026-09-26).

## Hard failures — outside every score above

**0 of 3 run(s) gated out of the quality means.** Counts by kind:

- `duplicate_stop`: 1 (counted, kept in means)

Leg-sanity defects kept in the means: 1.

## How the stage split is measured

The API does not expose the pre-rerank candidate pool: `debug` carries
`intent_source`, `constraints` and the validate `trace`, and the pool
`retrieve()` builds never reaches the response. The benchmark also has no
database access offline, so even a list of ids could not be scored against
reference coordinates. Stage-1 is therefore a **proxy**: for each graded
reference stop, is any returned stop within 250 m of it
(an upper bound on pool recall — a place the retriever never saw cannot
produce a returned stop next to it). The 250 m radius is deliberately much
tighter than the 750 m matcher used for recall@K, which
over-merges distinct nearby churches.

Stage-2 is the assembly quality *given* that: the conditional recall over the
reference stops the proxy found, plus precision, τ, detour, leg sanity and
budget fit — all of which the assembly decides.

> While the pool fallback is the route's own stops, stage-2 recall reads
> 1.000 **by construction** — the route cannot miss a stop stage 1 found,
> because stage 1 only looked at the route. It becomes a measurement the
> moment the API exposes a candidate pool larger than the route; until then
> read the other stage-2 numbers (precision, τ, detour, leg sanity), which
> do discriminate.

Grades: `must-see=3.0, nice-to-have=1.0, available=0.0`, ungraded treated as `must-see`.

## Determinism

`--replay` recomputes every metric from the recorded rows with no agent, no
Valhalla and no clock: two replays of the same snapshot are byte-identical,
which is what makes a metric change reviewable as a diff rather than a claim.
