---
name: run-quality
description: Measure route quality — the routing cases, the requirement-compliance cases, the tool-trajectory cases and the replayable evals. Use before a release, after changing the planner or the retrieval, or when asked whether the routes got better.
---

# Measure the routes

Four layers under `backend/quality/`, with different prerequisites:

| Layer | What it answers | Needs |
|---|---|---|
| routes | is the drawn route walkable and sensible (length, stops, budget, geometry) | a live stack |
| compliance | does the plan satisfy the stated requirements (hard/soft service, avoid, area, family) | a live stack |
| trajectory | which tools the agent called, with what arguments, and what it did with the answers — checked by code, never by a judge | offline: a scripted model, but a live db + Valhalla |
| evals | is the reading of the query stable and correct | replayable offline |

```bash
make quality                              # one-page report over what the runs wrote
cd backend && .venv/bin/python -m quality --help
cd backend && .venv/bin/python -m quality.trajectory   # the trajectory layer, on its own
```

`quality/runner.py` is the entry point; the golden cases are JSON under
`backend/quality/cases/{routes,compliance}/`, and the trajectory cases are one per
line in `backend/quality/cases/trajectory.jsonl`. A run writes into a report
directory; `--replay-golden DIR` re-runs a recorded set offline, which is what
makes a change comparable rather than merely green.

The trajectory layer replays the loop with a scripted `FunctionModel`, so it
measures what the *code* does with each call — the tool answers and the
termination behaviour — not what a real model chooses. Its teeth are the recorded
arguments and results, not the sequence.

## The honesty rule

The report prints «НЕ ИЗМЕРЯЛОСЬ» for a layer that did not run, and exits non-zero
when nothing was measured at all. An empty number and a zero are different
answers; never read the report as success without a count next to it.

Before blaming a change, check the stack: the routes and compliance layers need a
running agent, and *planning* needs an `OPENROUTER_API_KEY` — without one the
planning endpoints answer 503 `llm_not_configured` on purpose, and a quality run
will show nothing rather than something wrong.
