---
name: run-quality
description: Measure route quality — the routing cases, the requirement-compliance cases and the replayable evals. Use before a release, after changing the planner or the retrieval, or when asked whether the routes got better.
---

# Measure the routes

Three layers under `backend/quality/`, with different prerequisites:

| Layer | What it answers | Needs |
|---|---|---|
| routes | is the drawn route walkable and sensible (length, stops, budget, geometry) | a live stack |
| compliance | does the plan satisfy the stated requirements (hard/soft service, avoid, area, family) | a live stack |
| evals | is the reading of the query stable and correct | replayable offline |

```bash
make quality                              # one-page report over what the runs wrote
cd backend && .venv/bin/python -m quality --help
```

`quality/runner.py` is the entry point; the golden cases are JSON under
`backend/quality/cases/{routes,compliance}/`. A run writes into a report
directory; `--replay-golden DIR` re-runs a recorded set offline, which is what
makes a change comparable rather than merely green.

## The honesty rule

The report prints «НЕ ИЗМЕРЯЛОСЬ» for a layer that did not run, and exits non-zero
when nothing was measured at all. An empty number and a zero are different
answers; never read the report as success without a count next to it.

Before blaming a change, check the stack: the routes and compliance layers need a
running agent, and *planning* needs an `OPENROUTER_API_KEY` — without one the
planning endpoints answer 503 `llm_not_configured` on purpose, and a quality run
will show nothing rather than something wrong.
