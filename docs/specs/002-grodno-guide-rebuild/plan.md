# Plan 002 — технический план перестройки Grodno Guide

Способность описана в `spec.md` (ТЗ 002). Этот файл — исполнимый план: этапы, границы работ, замороженные интерфейсы и порядок проверки. Чек-лист задач — `tasks.md`.

## 0. Состояние на входе (проверено перед началом работ)

- Backend-тесты: `343 passed, 5 skipped` (`.venv/bin/python -m pytest tests -q`) — зелёная база, от которой можно откатываться.
- Живая БД измерена (2026-09-26, контейнер `grodno-db`, `demo_pgdata` цел): **3712 POI**. Распределение по категориям: архитектура 1252, кафе 593, памятник 557, замок 294, **туалет 272**, гостиница 208, костёл 170, церковь 126, ресторан 109, музей 96, храм 18, монастырь 8, парк 4, дворец 3, инфраструктура 2. Вывод: данные по туалетам и кафе в БД есть — пустой результат даёт не отсутствие данных, а мэппинг категорий в `search.db_categories()`, который и должен закрыть W1.
- Frontend: Vite 7 + React 18 + TS strict, MapLibre, TanStack Query, Zustand; тесты Vitest, конвенции — `frontend/CLAUDE.md` (KEBAB_CASE файлы, `*.spec.ts(x)` рядом с исходником).
- Docker: существующие контейнеры `grodno-db/valhalla/frontend` повреждены (RW-layer nil) — образы пересобираются, volume `demo_pgdata` цел.
- Рабочее дерево содержит незакоммиченные изменения предыдущих фаз (карта, sidebar, ingest-скрипты, README, spec). Перезаписывать их нельзя, коммитить — только по команде.
- `backend/agent/requirements.py` — новый замороженный контракт смысла запроса. `GenerateReq` расширен явными полями фильтров.

## 1. Целевая схема потока

```
POST /routes/generate
  → build_requirements(query + явные фильтры)      → TripRequirements   (этап понимания)
  → resolve/ground (pg_trgm + aliases + areas)     → реальные ID/коды
  → retrieve (две полосы: sights / services)       → кандидаты + причины отсева
  → cost matrix (Valhalla, включая origin)         → CostMatrix
  → optimize (hard обязательны, soft по желанию)   → порядок остановок
  → final /route (Valhalla)                        → geometry + maneuvers
  → verify(TripRequirements, plan, geometry)       → satisfied / unmet / uncertain
  → TripPlan                                       → карта + «Проводник»
```

Инварианты, которые проверяются тестами, а не комментариями:

1. Hard-требование либо доказано (`satisfied` с `place_ids`), либо возвращается `unmet`/`uncertain` с машинной причиной. Молчаливое удаление обязательной точки запрещено.
2. Состав группы, бюджет и жёсткие удобства переживают refinement и повторное планирование.
3. Ни одна выданная точка не вне Гродненской области.
4. Геометрия, манёвры и времена согласованы между собой и приходят из одного ответа.

## 2. Замороженные интерфейсы между потоками работ

Изменять их можно только вместе с этим файлом, чтобы параллельные потоки не разошлись.

- `agent/requirements.py` — `Requirement`, `PartyComposition`, `TripRequirements` (+ методы `hard()`, `hard_service_codes()`, `must_visit_ids()`, `failed_hard()`, `is_ready()`, `public_requirements()`).
- `agent/models.py::GenerateReq` — явные фильтры: `locale`, `party_adults`, `party_children`, `party_children_ages`, `mobility`, `hard_services`, `interests`, `avoid`, `result_mode`, `round_trip`.
- `agent/taxonomy.py` (появляется в W1) — `Category(code, ru, en, osm_tags, role, visit_minutes)`, `resolve_code(term, locale)`, `db_values(codes)`, `all_codes()`. Единственный источник кодов категорий для импорта, поиска, стоимости и API.
- Причины отказа верификатора — машинные коды (`reason`), локализация на стороне API/UI, не русская строка в ядре.

## 3. Разбиение на потоки работ (непересекающиеся файлы)

| Поток | Владеет файлами | Результат |
|---|---|---|
| W1 Таксономия и категории | `backend/agent/taxonomy.py` (new), `backend/data/taxonomy.csv` (new), `backend/agent/constants.py`, `backend/agent/search.py`, `backend/tests/test_taxonomy.py` (new) | один источник кодов; `db_categories()` покрывает кафе/ресторан/туалет/гостиницу; поиск по алиасам RU/EN |
| W2 Понимание запроса | `backend/agent/planner/intent.py`, `backend/agent/planner/resolve.py`, `backend/tests/test_requirements_extraction.py` (new), `backend/tests/test_degraded_intent.py` | `TripRequirements` из текста (RU/EN) + явных фильтров; истинные hard/soft; состав группы |
| W3 Верификатор и оптимизатор | `backend/agent/planner/validate.py`, `backend/agent/planner/cost.py`, `backend/agent/planner/optimize.py`, `backend/agent/planner/explain.py`, `backend/agent/planner/verify.py` (new), `backend/tests/test_verify_requirements.py` (new) | проверка маршрута против требований; обязательная точка не исчезает молча |
| W4 Данные и seed | `backend/db/migrations/*`, `backend/scripts/seed_all.py` (new), `backend/tests/test_seed_pipeline.py` (new), `backend/data/README*` | идемпотентный seed с отчётом покрытия; миграции `place_aliases`/`place_sources`/`areas` |
| W5 Frontend: фильтры | `frontend/src/components/sidebar.tsx` (+`.spec.tsx`), `frontend/src/components/parts/segmented.tsx` | расширенные фильтры (группа, обязательные удобства, интересы, режим) уходят в API |
| W6 Frontend: «Проводник» | `frontend/src/components/guide-panel.tsx`, `frontend/src/components/parts/guide-*.tsx` | пешеходный навигатор: манёвр, прогресс по линии, off-route, ручной режим |
| W7 Локализация RU/EN | `frontend/src/i18n/*` (new), `frontend/src/**` (замена строк), `frontend/package.json` | один i18n-слой, обе локали, тест полноты |
| W13 Агентный интерпретатор (PydanticAI) | `backend/agent/planner/agent_interpret.py` (new), `backend/agent/tools.py` (new), `backend/pyproject.toml`, `backend/tests/test_agent_interpret.py` (new) | агент с ограниченными инструментами заполняет `TripRequirements`; без ключа — детерминированный fallback |

Решение по фреймворку зафиксировано в `spec.md` §4.2: агент на PydanticAI входит в объём, замер выбирает модель, а не «нужен ли фреймворк». Поток W13 не пересекается с W2: W2 строит детерминированный `build_requirements` (в т.ч. fallback), W13 — агентный путь в отдельном модуле; интеграция обоих — на этапе B.

Поток W7 запускается **после** W5/W6: он трогает те же компоненты. W5 и W6 не создают своих словарей — строки берут из существующих констант, чтобы W7 заменил их централизованно.

## 4. Этапы и точки принятия решений

- **Этап A (сейчас): W1–W6 параллельно.** Каждый поток обязан оставить зелёными `pytest` и `vitest`, добавить тесты на своё поведение и не выходить за свои файлы.
- **Этап B: W7 (i18n) + интеграция.** Свести новые поля запроса в pipeline, включить `requirements` в ответ, вывести список требований в UI.
- **Этап C: проверка на живом стеке.** Поднять db/valhalla/frontend, прогнать приёмочные сценарии из `spec.md` §9, обновить golden set и benchmark.
- **Точка решения по OR-Tools и PydanticAI:** сравнительный прогон на одном пуле и одной матрице; решение фиксируется здесь же, до включения зависимости.

## 5. Как проверяется каждый поток

- Backend: `cd backend && ./.venv/bin/python -m pytest tests -q` (полный набор) и точечно новый тест потока.
- Frontend: `cd frontend && npm run typecheck && npx vitest run <файл>`; для W5/W6 дополнительно `npm run lint`.
- Живая проверка (Этап C): `docker compose up -d db valhalla`, backend локально, `curl /routes/generate` с приёмочными запросами RU и EN; результат приложить текстом, а не пересказом.
- Ничего не объявляется пройденным без фактического запуска команды.
