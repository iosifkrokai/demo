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
  acceptable answer must meet. The loader refuses unknown keys, so the schema is
  enforced. See `cases/compliance/README.md` for the field reference.
* **evals** (`cases/<stage>.jsonl`): `verdicts` (offline verifier), `services`
  (PostGIS, needs the DB), `interpretation` (live model), `plan` (live app).
  Every line carries a `why`; a malformed line is a loud error, never a silent
  skip; `known_gap` is visible but neither counted nor gating.

## What no layer measures

Human taste, the mobile card layout, tile translation, regions other than Grodno,
and the real detour *minutes* to reach a point beside the line (`services` measures
the distance to the line, not a Valhalla walk). These stay unmeasured on purpose.
