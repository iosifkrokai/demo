# Tests

Three kinds, and only three. The collection stays small on purpose: coverage of
the whole app is not the goal, and a test that does not pay for itself is deleted.

## 1. Endpoint success coverage — `test_endpoints.py`

One success case per HTTP endpoint. The list is enumerated from
`backend/api/routers/`, one test function each: health, routes, catalogue,
accounts, admin. A new endpoint gets exactly one success test here and
nothing else; error branches come later, one campaign at a time.

These tests run against the live stack at `GRODNO_BASE_URL` (default
`http://localhost:8080`). `live_stack` probes `/health` and every test that uses
it **skips itself when the stack is unreachable**. A skip is not a pass — it says
the stack was not there to check, or lacked a dependency the endpoint needs:

- the planning endpoints (`/routes/generate`, `/routes/reroute`,
  `/routes/explain`, `/routes/trace`, `/routes/progress`) skip when `llm=false`,
  because without a reader the request is refused with `503` by design;
- the admin endpoints skip unless `GRODNO_ADMIN_EMAIL` and
  `GRODNO_ADMIN_PASSWORD` name a real administrator.

Do not invent endpoints or response fields: read the router and its wire model,
then assert the keys the model declares.

## 2. Pure leaf math — `test_routing_math.py`

Unit tests of `backend/routing/` with no database, no network and no model. Pin
the functions that decide something — the time sentinels in
`routing/valhalla/types.py`, the snap radii, the failure markers. If a function
needs a connection or a provider to run, it is not this kind of test.

## 3. Offline trajectory — `test_agent_trajectory.py`

A `FunctionModel` stands in for the LLM so an agent run replays with no network
and no API key. The `fake_model` fixture patches the single provider seam,
`agent.llm.make_model`, and hands back a queue the test programs:

```python
def test_something(fake_model):
    fake_model.call("search_places", query="замок")  # one tool call
    fake_model.finish(budget_minutes=90)  # the structured reading
    result = interpret_with_agent("замок на полтора часа", ReaderBrief())
    assert result.budget_minutes == 90
```

`call(name, **args)` queues a tool call the agent answers from its own tools;
`finish(**fields)` queues the `AgentReading` that ends the run. The queue is
consumed one model turn per agent step, so the order written is the order played.
The whole agent path runs — instructions, the tool loop, the structured output,
the mapping into `TripRequirements`.

## Fixtures — `conftest.py`

- `_no_real_model_requests` (autouse): sets
  `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`. A test that reaches a real
  provider fails loudly instead of being slow.
- `_empty_caches` (autouse): clears `core.cache.INTERPRET_CACHE` and
  `EMBED_CACHE` before and after every test, so tests cannot leak into each other.
- `fake_model`: a programmable `FunctionModel` behind `agent.llm.make_model`.
- `live_stack`: the running stack, or a skip. Probes `/health` once per session.

## Architecture — `test_layer_boundaries.py`

Not coverage. It holds the layering the refactor promises: `routing/` and
`agent/` are leaves that never reach `agent`, `planner` or `api`; the logic
layers speak no SQL and import no driver; `db/models/**` imports the standard
library only. It parses with `ast`, so prose in a docstring is not a violation.
