# Tasks 005 — аккаунты и роли, посещённые места, админка

Чек-лист реализации. Отмечено выполненное.

## Backend

- [x] T1 `db/migrations/0008_accounts_visits.sql`: `CREATE EXTENSION citext`; `users`,
      `user_sessions`, `visited_places` + индексы (idempotent).
- [x] T2 `db/init.sql`: citext + зеркало DDL из T1.
- [x] T3 Применён `0008` на живой БД (idempotent; проверено `\dt`/`\d`).
- [x] T4 `agent/passwords.py`: `hash_password`, `verify_password`, `new_session_token`,
      `hash_token`, `SESSION_TTL_S`.
- [x] T5 `agent/accounts_models.py`: модели + константы кодов + `normalize_email`/
      `password_problem`/`public_user`.
- [x] T6 `agent/accounts_store.py`: протокол `AccountRepository` +
      `PostgresAccountRepository` (users/sessions/visited/places/stats),
      `StorageUnavailable`/`EmailTaken`/`DuplicateSource`.
- [x] T7 `agent/accounts_api.py`: `/auth/register|login|logout|me`, `/me/visited` (+`/{id}`,
      bulk), `/admin/users`, `/admin/users/{id}`, `/admin/places`, `/admin/places/{id}`,
      `/admin/stats`; `_require_user`/`_require_admin`; `_storage_guarded`.
- [x] T8 `agent/main.py`: `include_router(accounts_api.router)`; CORS `allow_credentials=True`.
- [x] T9 `scripts/create_admin.py`: идемпотентный первый админ.
- [x] T10 `tests/test_accounts.py`: fake-репозиторий (47 тестов) + живой интеграционный
      тест (citext, upsert визита, curated-guard) — 48 passed.
- [x] T11 Гейты: `ruff check` (мои файлы чисто), `pyright` (0 ошибок, только warnings),
      `pytest` (48/48 для моих; 6 падений в suite — предсуществующие живые тесты).

## Frontend

- [x] T12 `api/types.ts`: wire-типы аккаунта/визита/админки.
- [x] T13 `api/account.ts`: register/login/logout/me, visited list/mark/unmark/bulk,
      admin users/places/stats; normalizers + `AccountApiError`.
- [x] T14 `hooks/use-auth.ts`, `hooks/use-visited.ts`, `hooks/use-admin.ts`.
- [x] T15 `components/account/account-bar.tsx` + рендер в `root-component.tsx`.
- [x] T16 `components/auth/login-page.tsx`, `register-page.tsx`.
- [x] T17 `components/admin/admin-page.tsx` (+ `users-panel.tsx`, `places-panel.tsx`).
- [x] T18 `components/visited/visited-page.tsx`, `visited-toggle.tsx`; тумблер встроен
      в карточку места (`place-card-body`, `place-card-popup`, `mobile-place-card`, map).
- [x] T19 `routes.tsx`: маршруты `/login`, `/register`, `/admin`, `/visited`.
- [x] T20 `nginx.conf` + `vite.config.ts`: прокси `/auth/`, `/me/`, `/admin/`
      (bare `/admin`,`/login`,`/visited` остаются SPA).
- [x] T21 Тесты: `api/account.spec.ts` (6), `visited-toggle.spec.tsx` (3) + моки в
      4 card/map-спеках, чтобы они не требовали QueryClient.
- [x] T22 Гейты: `typecheck` чисто, `vitest run` — 1360/1360, `prettier --check` чисто,
      `eslint` — мои файлы чисто (9 замечаний в suite — предсуществующие).

## Приёмка

- [x] T23 Живой прогон: агент из host-venv против live-БД на :8081 — register(201) →
      duplicate(409) → weak(422) → me → visited(idempotent) → bulk(skips unknown) →
      user/admin(stats 403) → admin after role bump (200) → curated-guard held →
      logout(204) → сессии только хешами. Тестовые данные удалены.
      **Не** пересобирал docker-стек: live-контейнеры подняты из другого чекаута
      (`~/Projects/demo`) и корневой `.env` отсутствует (compose не поднимется) — деплой
      остаётся за владельцем.
- [x] T24 `README.md` (§5a + nginx-заметка) и `DEMO.md` (Сцена 6) обновлены.

## Обновление — вход обязателен

- [x] T25 `utils/auth-guard.ts` (`loginRedirectFor`/`returnPathOf`) + `auth-guard.spec.ts` (4 теста).
- [x] T26 Гейт `requireAuth` в `beforeLoad` на `/`, `/$activeTab`, `/visited`, `/admin`
      (общий `authQueryOptions` с `useAuth`, fail-closed).
- [x] T27 `loginSearchSchema` (`?redirect=`) на `/login` и `/register`; формы возвращают
      на исходный путь (`window.location.assign`).
- [x] T28 `AccountBar` скрыт на `/login` и `/register`.
- [x] T29 Гейты: `typecheck` чисто, `vitest run` — 1364/1364, `prettier --check`/`eslint` чисто.
- [x] T30 Проверено в Docker: аноним на `/directions`, `/visited`, `/admin` → `/login?redirect=…`;
      после входа — приложение. Спека §5a + приёмка обновлены.

## Обновление — карта в админке (двигать точки)

- [x] T31 `components/admin/admin-place-map.tsx` — маленькая карта с перетаскиваемым маркером
      (+`admin-place-map.spec.tsx`, 3 теста).
- [x] T32 `components/admin/place-fields.ts` — чистая функция «форма → PATCH»; координаты
      **опускаются** при пустом поле (не `null`) + 6 тестов.
- [x] T33 `places-panel.tsx` — две колонки (список + карта), «на карте», поля широты/долготы,
      drag → draft → `PATCH`.
- [x] T34 Бэкенд: `AdminPlacePatch` запрещает `null` в `lat`/`lon` (`422`, а не `500` от
      `NOT NULL`) + тест; спека §3 обновлена.
- [x] T35 Гейты: `typecheck`/`vitest`/`prettier`/`eslint` чисто; бэкенд `ruff`/`pyright`/
      `pytest` (49).
- [x] T36 Проверено в Docker: точку видно; маркер тащится; «сохранить» переносит точку
      (координаты изменились в БД).

