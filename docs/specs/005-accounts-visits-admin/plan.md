# Plan 005 — аккаунты и роли, посещённые места, админка

> **Для агента:** реализуется по шагам из `tasks.md`; спеку см. `spec.md`.

**Goal:** добавить вход по email+паролю с ролями `user`/`admin`, персональные «посещённые
места» на сервере и админский раздел (пользователи + места), не ломая анонимного клиента
(spec 003).

**Архитектура:** новый capability-модуль рядом с `clients_*`: `accounts_models.py` (pydantic,
машинные коды), `accounts_store.py` (репозиторий на Postgres, тот же паттерн, что
`clients_store.py`), `accounts_api.py` (один APIRouter, подключается одной строкой в
`main.py`). Аутентификация — серверные сессии в таблице, токен в HttpOnly-куке; пароль —
scrypt из stdlib. На фронте — отдельные страницы `/login`, `/register`, `/admin`, `/visited`
и контрол аккаунта поверх карты.

**Стек:** FastAPI, psycopg3, Postgres/citext, pydantic v2; React 18 + TanStack Router/Query +
Zustand, vitest.

---

## Ключевые решения

| Решение | Выбор | Почему |
|---|---|---|
| Модель входа | email+пароль, серверные сессии, HttpOnly-кука | нет токена в JS → нет XSS-кражи; отзыв сессии удалением строки |
| Хеш пароля | `hashlib.scrypt` (N=2¹⁵,r=8,p=1) | stdlib, без новой рантайм-зависимости и сборки; формат самодокументирован |
| Токен сессии | `secrets.token_urlsafe(32)`, в БД `sha256` | утечка БД не даёт войти |
| Связь с анонимом | `users.client_id → clients(id)` on delete set null | «повышение клиента до аккаунта» из spec 003 §1 без смены модели |
| Посещённые | `visited_places(user_id, place_id)` | durable-метка; прогресс прогулки остаётся в localStorage |
| Первый админ | скрипт `scripts/create_admin.py` | не security-hole «первый зарегистрированный = админ» |
| Изоляция хранилища | отдельный `PostgresAccountRepository` | не раздувать `clients_store`; тесты подменяют `app.state.accounts_repository` |

## Файлы

**Backend — создать**
- `backend/db/migrations/0008_accounts_visits.sql`
- `backend/agent/passwords.py` (hash/verify + токены)
- `backend/agent/accounts_models.py`
- `backend/agent/accounts_store.py`
- `backend/agent/accounts_api.py`
- `backend/scripts/create_admin.py`
- `backend/tests/test_accounts.py`

**Backend — изменить**
- `backend/db/init.sql` (citext + зеркало DDL)
- `backend/agent/main.py` (include_router, allow_credentials/CORS-заголовки)
- `backend/pyproject.toml` — не требуется (stdlib scrypt)

**Frontend — создать**
- `frontend/src/api/account.ts`
- `frontend/src/hooks/use-auth.ts`, `use-visited.ts`, `use-admin.ts`
- `frontend/src/components/account/account-bar.tsx`
- `frontend/src/components/auth/{login-page,register-page}.tsx`
- `frontend/src/components/admin/{admin-page,users-panel,places-panel}.tsx`
- `frontend/src/components/visited/{visited-page,visited-toggle}.tsx`
- тесты: `frontend/src/api/account.spec.ts`, `frontend/src/components/visited/visited-toggle.spec.tsx`

**Frontend — изменить**
- `frontend/src/routes.tsx` (новые маршруты)
- `frontend/src/app.tsx` (AccountBar)
- `frontend/src/api/types.ts` (wire-типы)
- `frontend/nginx.conf`, `frontend/vite.config.ts` (`/auth`, `/me`, `/admin`)

## Шаги (детально — в tasks.md)

1. **Миграция + init.sql.** `citext`, `users`, `user_sessions`, `visited_places`; применить
   на живой БД `docker exec grodno-db psql -f`.
2. **`passwords.py`.** scrypt hash/verify + `new_session_token`/`hash_token`; юнит-тесты.
3. **`accounts_models.py`.** Pydantic In/Out + константы кодов; `PublicUser` без хеша.
4. **`accounts_store.py`.** Репозиторий + протокол; правка места — в явной транзакции с
   `set_config('grodno.allow_curated_category_change','on', true)`.
5. **`accounts_api.py`.** `/auth/*`, `/me/visited*`, `/admin/*`; `require_user` /
   `require_admin` зависимости; `_storage_guarded` как в `clients_api`.
6. **`main.py`.** `include_router(accounts_api.router)`; CORS: `allow_credentials=True`,
   заголовки `Content-Type, X-Client-Id`.
7. **`scripts/create_admin.py`.** Идемпотентный первый админ.
8. **`tests/test_accounts.py`.** Fake-репозиторий; вход/роли/визиты/админ-гарды/503.
9. **Frontend API+hooks.** `account.ts` (normalize как в `client.ts`), `use-auth/visited/admin`.
10. **Frontend UI.** AccountBar, login/register, admin (2 вкладки), visited, кнопка-тумблер.
11. **Маршруты + прокси.** `routes.tsx`, `app.tsx`, nginx, vite.
12. **Гейты.** `ruff check .`, `pyright`, `pytest -q`; `npm run typecheck`, `npm test`.
13. **Живой прогон.** Поднять агент из hoст-venv на :8081 против live-БД, прогнать сценарий
    curl: register → login → visited → admin (403 у user, 200 у admin).

## Проверка

```bash
# backend
cd backend && .venv/bin/ruff check . && .venv/bin/pyright && .venv/bin/python -m pytest -q
# frontend
cd frontend && npm run typecheck && npm test -- --run
# живой прогон (host venv против live-БД)
DATABASE_URL=postgresql://grodno:***@localhost:5432/grodno VALHALLA_URL=http://localhost:8002 \
  .venv/bin/python -m uvicorn agent.main:app --port 8081
curl -c /tmp/cj -X POST localhost:8081/auth/register -d '{"email":"a@b.c","password":"secret123"}'
```

## Риски и открытые вопросы

- **Cookie и cross-origin.** В демо фронт и агент — один origin (nginx/Vite-прокси), поэтому
  кука работает; при `VITE_AGENT_URL` на другой хост нужен `credentials:'include'` + CORS
  с `allow_credentials=True` (сделано) и `SameSite=None; Secure` (не сделано, задокументировано).
- **Curated-guard.** Правка `category` админом требует `set_config(..., true)` в транзакции —
  проверяется живым прогоном, а не только тестом с фейком.
- **`citext`.** Расширение может быть недоступно на экзотическом Postgres; здесь официальный
  `postgres:16`-образ — есть.
- **Смена формата пароля.** Старые хеши (argon2) не мигрируются — их и нет.
