# Plan 001 — Grodno Region Coverage

## Context
- Stack: Postgres/PostGIS/pgvector `places`, FastAPI-агент (agent/planner/*), Valhalla,
  Vite webapp. Данные сейчас: scrape planetabelarus.by (Гродно-область!) + ручная
  вычитка `data/places_curated.csv` (id-специфичная, привязана к старой БД).
- Известные проблемы: (1) apply_curated привязан к SERIAL id старой БД; (2) пайплайн
  падает без OPENROUTER_API_KEY; (3) колонки hours/price/district в БД нет.

## Approach
1. **Schema**: `db/migrations/0002_region_fields.sql` —
   `opening_hours TEXT`, `ticket_price TEXT`, `visit_minutes INT`,
   `district TEXT`, `town TEXT` (+ индекс по district). Обновить `db/init.sql`
   до полной формы (включая fun_fact/fun_facts/links из 0001).
2. **Dataset**: `data/places_region.csv` — pipe-delimited, header-строка,
   самоописательный (включает координаты), ~70 мест по 17 районам + ключевые
   места города Гродно не дублируются (город покрывает scraper). Источники —
   Wikipedia/официальные страницы; часы/цены ориентировочные.
3. **Seed**: `scripts/seed_region.py` — upsert по `source_url` (`region:<slug>`),
   валидация категорий и координат против bbox области, `--dry-run`,
   опционально `--embed` (OpenRouter, если есть ключ). Не трогает scrape-строки.
4. **Agent**: добавить колонки в SELECT'ы `agent/search.py`, в `Candidate` и
   `Place` (`agent/models.py`), пробросить в `pipeline.py`. Keyword-fallback в
   `retrieve`, когда вектора недоступны; `/health` без паники.
5. **Webapp**: карточка места (place-card-popup) показывает часы/цены —
   делегируется Codex-агенту, contract = поля `opening_hours`/`ticket_price`
   в JSON ответа.
6. **Verify**: миграция на живой БД → seed → smoke-тесты через curl
   (город, область, keyword-режим без ключа).

## Risks / Tradeoffs
- Часы/цены меняются: поля текстовые «ориентировочно», источник в `links`.
- Свежесканированные строки planetabelarus (область!) конфликтуют по имени с
  region-датасетом: seed обновляет только строки `region:*`, дубликаты сущностей
  возможны (поиск в упор по имени не сломан — RRF/rerank разберётся).
- Эмбеддинги без ключа недоступны: keyword fallback ловит только короткие
  запросы; это честная деградация (задокументирована).

## Open Questions
- Подтверждение часов/цен museums по официальным источникам — вне скоупа (WON'T).