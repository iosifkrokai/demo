# Tasks

## Убрать Langfuse и самодельную телеметрию → Logfire

**Решение (2026-10-11):** Langfuse выпиливается вместе со всей ручной обвязкой
трассировки; наблюдение ставится на **Logfire** — родной для PydanticAI.

### Сделано (коммит `ffa7ae2`)

- `langfuse` убран из зависимостей и из кода; `pydantic-ai-slim[anthropic,openrouter,logfire]`.
- `logfire.configure(send_to_logfire="if-token-present", token=LOGFIRE_TOKEN)` +
  `logfire.instrument_pydantic_ai()` в `api/main.py` — вызовы модели и вызовы тулзов
  видны без единой рукописной строки.
- `agent/telemetry.py` **удалён**: всё, что он делал, Logfire делает сам.
- `telemetry/trace.py` потерял экспорт в Langfuse и `core.config`-зависимость —
  остался только счётчик спанов в памяти.
- `LANGFUSE_*` и `langfuse_configured()` убраны из `core/config.py`.
- Два осиротевших эндпойнта (`/routes/trace/{id}`, `/routes/progress/{id}`) сняты;
  фронтенд их не использовал.
- `.env.example` переведён на `LOGFIRE_TOKEN`; `uv.lock` обновлён.

### Осталось

`telemetry/` как пакет **ещё существует** — его держат 93 вызова `trace.` в
`planner/`. Удаляется вместе с `planner/` на фазе 5 основного рефакторинга;
тогда же уходит `progress.py`. Чистить эти вызовы раньше — значит править код,
который будет удалён целиком.

**Готово, когда:** `telemetry/` удалён и в `planner/` не осталось ни одного
`trace.`/`progress.`
