# Tasks

Открытые работы: агентский путь и фронтенд. Выполненное отсюда вычищается, а не
помечается галочкой: список — это очередь, а не журнал.

## 1. Агент не умеет отказывать и не выбирает остановки за пользователя

**Самое важное.** На живых прогонах два самых частых вида запроса не дают ничего.

```
'Гродно за два часа'       → 0 остановок, 2 обращения к модели, bad_argument
'два часа с двумя детьми'  → 0 остановок, 1 обращение, bad_argument
'прогулка по Вильнюсу'     → 0 остановок, bad_argument
```

Первые два — «территория + время». Модель рассуждает верно («названа только
территория и время, объектов нет») и кладёт причину в `unknowns`, но **остановки
теперь не выбирает никто**: в конвейере это делал шаг retrieve. Раньше «два часа
по Гродно» возвращало прогулку, сейчас — пустоту. Оговорка к записям:
`bad_argument` — код ошибки **тула** (`agent/tools/__init__.py:16`), а не статус
плана; на проводе статусы — `ready|infeasible|degraded|pending`
(`api/models/routes.py:40`), и на пустой сдаче клиент получает `pending` с нулём
точек (`agent/client.py:41-44`, `api/responses.py:63`).

Третий — выход за покрытие. Модель **сама** распознала («Вильнюс — за пределами
обслуживаемого региона»), но выразить отказ нечем: в сдаче нет такого исхода.
Старый конвейер отдавал честный `infeasible`; сейчас клиент получит `pending` без
точек.

**Что сделать.** В контракт сдачи (`PlanSubmission`) добавить явный исход —
`route` | `catalogue` | `refuse` — и правило в промпт: когда названы только
территория и время, остановки выбирает агент, это его работа, а не повод сдать
пустой план. Отказ должен доезжать до ответа как `status="infeasible"` с причиной,
а не как ошибка тула.

## 2. Удалённые пользователем остановки защищены только промптом

Пользователь убрал остановку руками — её id уезжает модели заметкой
`deleted_stop_ids` (`agent/prompts/notes.py:76`), и на этом всё. В коде её **никто
не вычитает**; старый конвейер звал `drop_excluded`.

Для сравнения: базовые остановки закреплены кодом (`client._with_base_stops`
добавляет их в `must_keep_ids`). Вышло непоследовательно — одно гарантировано,
другое честным словом модели.

**Что сделать.** В `client.plan_with_agent` вычесть `brief.context.excluded_ids` из
`ordered_ids` перед сдачей — так же, как туда добавляются базовые.

## 3. Харнесс измерения не переписан

Три слоя из плана (исход / траектория / шаг) не построены. Старый `quality/`
частично жив: стадии `verdicts` и `interpretation` я перенаправил на новый путь,
но раннер исхода всё ещё цепляется за старую форму `debug` и за старый контракт
ответа. Мерить «лучше или хуже» сейчас нечем.

**Что сделать.** Оставить чистые функции счёта из `quality/runner.py`
(`haversine_km`, `match_golden_to_ours`, `shortest_walk_order`, `kendall_tau`,
`detour_km`, проверка ног, `bootstrap_ci`, `noise_floor`) и переписать раннер под
новый контракт. Добавить то, чего раньше не было: **оценку траектории** — какие
тулзы агент звал и с какими аргументами (проверка кодом, не судьёй). Сохранить
правило честности: неизмеренный слой печатается как «НЕ ИЗМЕРЯЛОСЬ» и никогда не
показывается зелёным — оно уже живёт в `quality/report.py:316,328,343,502`, то
есть это «не сломать», а не «завести».

Заодно в самом раннере: `_status_from_requirements` (`quality/runner.py:2328`) подписан
как копия удалённого `planner/verify.py::overall_status`, но **разошёлся** с живым
`routing/verify.py:517-534` — зеркало объявляет `infeasible` при любом невыполненном
жёстком требовании, настоящий код только для причин из `INFEASIBLE_REASONS`
(`routing/verify.py:90-97`). Вызывать живой `overall_status` вместо зеркала. И словарь
харнесса всё ещё знает `rejected`/`needs_clarification` (`quality/runner.py:36-47`,
`quality/README.md:82-83`, `quality/cases/compliance/out_of_region_vilnius.json`) —
новый API таких статусов не выдаёт (`api/models/routes.py:40`).

**Важно про baseline.** Сравниться со *старым* конвейером больше нельзя: его код
удалён, он остался только в git-истории. Если «стало лучше» относительно конвейера
нужно числом — придётся выкатить `master` отдельно и прогнать его там.

## 4. Серверного треда нет

Уточнение работает: инструкция едет в брифе, базовые остановки переживают любой
предложенный план. Но журнала ходов нет — переиграть ход нельзя, и клиент
по-прежнему шлёт снапшот заново каждый раз
(`frontend/src/components/sidebar.tsx:862-872`; в `stores/directions-store.ts`
вызовов `fetch` нет вовсе — его `snapshotRoute()` на `:628` кладёт снимок в
локальный undo-стек).

**Что сделать.** `agent/thread.py` поверх нового репозитория в `db/store/`; образец
формы — родитель и строки, как у `db/models/itinerary.py` + `db/store/itineraries.py`
(у самого `db/store/itineraries.py` сейчас только кураторские прогулки). Таблица ходов
`(thread_id, revision, query, submission, measurement, created)` — новая:
`db/models/route.py`, на который опирался этот пункт, удалён вместе с анонимным
клиентом (коммиты `5af1956`, `372fd5f`), от него остался только
`__pycache__/route.cpython-312.pyc` без исходника. `POST /routes/generate` продолжает
принимать `context`, но сервер сверяется с журналом и не доверяет снапшоту больше
него.

## 5. Кэш измерений и деградация под дедлайном

Стенные часы есть (150 с на генерацию — `agent/runner.py:33`, 40 с на уточнение —
`core/constants.py:10`), и проверки остатка в тулзах написаны
(`agent/tools/plan.py:124`, `agent/tools/submit.py:126`, `agent/tools/services.py:130`) —
**но не срабатывают**: `PlanningDeps.deadline_at` (`agent/schema.py:80`) не присваивается
нигде, `client.plan_with_agent` строит deps без него (`agent/client.py:80`), поэтому
`seconds_left()` всегда `inf` (`agent/schema.py:82-86`) и ни один тул по времени не
отказывает. Чего нет:

* **живого дедлайна** — довести остаток от `api/routers/routes.py:60` до
  `PlanningDeps`, иначе и кэш, и деградация встанут на неподключённую механику.
  Побочно: `REQUEST_DEADLINE_S = 240` (`core/constants.py:8`) мёртв —
  `agent/runner.py:129-131` режет его до 150; либо снять, либо объяснить;
* **кэша измерений** — по ключу `(sorted stop_ids, costing, budget, round_trip,
  версия данных)`. Вальгалла детерминирована, и именно это делает уточнение
  дешёвым. Оговорка: понятия «версия данных» в дереве нет ни в каком виде — его
  надо завести, либо вынуть из ключа;
* **деградации в цикле** — когда время на исходе, раннер должен подсказать агенту
  сдаваться, а не ждать, пока тулзы начнут отказывать. Сегодня единственная
  подсказка «сдавайся» — счётчик тулов, а не время (`agent/runner.py:30`,
  `prompts/rules.py:25-27`).

## 6. Мелочи

* **`find_places` без фильтра по территории.** Отсюда пляски модели с запросами по
  городам (в каталоге костёлов — четыре вызова вместо одного).
* **`db/data/README.md`** описывает наборы данных, но не то, что `taxonomy.csv` и
  `itineraries.json` теперь **проектируются в таблицы** (`categories`,
  `itineraries`, `itinerary_stops`).
* **`quality/README.md`** — нарезка стадий совпадает с кодом
  (`quality/evals.py::STAGES` и имена файлов кейсов), протухло другое: словарь
  статусов (`quality/README.md:82-83`) знает `rejected`/`needs_clarification`, которых
  новый API не выдаёт, а проза «did the request survive the pipeline?»
  (`quality/README.md:8`) говорит о конвейере, которого нет.
* **Территорию фильтровать нечем.** Поля под это есть, вызовов нет:
  `GenerateReq.region_bbox` (`api/models/routes.py:89`),
  `ResolvedConstraints.area_anchor` (`routing/models.py:91`) и
  `places.by_embedding(region_bbox=…)` (`db/store/places.py:222`) не вызываются ни
  откуда.
* **Осиротевший байткод.** Исходники удалённых модулей остались в кэше:
  `agent/__pycache__/{cache,interpret_cache,model,telemetry}`,
  `db/models/__pycache__/{client,route}`,
  `db/store/__pycache__/{accounts_store,clients,clients_store,place_fields,search}`.
* **`tests/test_layer_boundaries.py:31-37`** всё ещё перечисляет `planner` в
  `LEAF_RULES` (`:31`) и `LOGIC_LAYERS` (`:37`), хотя пакета нет; докстринг (`:12-13`)
  это признаёт.
* **Болтливость.** Простой запрос по Старому городу: 7 обращений к модели, 43 с;
  бюджет тулзов — 12 вызовов, и каталог подошёл к границе.

## Известные пробелы, оставленные намеренно

Видны в `quality.evals`, но провалом не считаются:

* «без храмов» запрещает только код «храм», а костёлы, церкви и монастыри в него не
  входят — в данных нет иерархии категорий;
* «Гродно за два часа» модель делает остановкой сам город (см. пункт 1);
* «Старый и Новый замки» может разъехаться на обрывки имён.

## Фронтенд — остатки аудита (C6–C9)

Метки C6–C9 — из аудита фронта (`B` — блокирующее для релиза, `M` — можно после).
C1–C4 сделаны, C5 (серверные сохранённые маршруты) закрыт удалением фичи целиком.
Ссылки ниже перепроверены по текущему дереву: часть путей из исходного плана уже не
существует, здесь остались только живые.

## 7. C6 — Доступность и всплытие ошибок `[B]`

Строгие правила `jsx-a11y` не включены, иконочные кнопки без доступных имён, а провал
запроса «Посещённых» не доезжает до пользователя.

В `frontend/eslint.config.js:51-62` сейчас шесть правил, и все — `warn` (седьмое в
файле, `react/jsx-no-target-blank` на `:63`, — `off`). Проверил:
`rg` по `error|isError|onError` в `frontend/src/components/visited/visited-page.tsx` пуст,
хотя хуки ошибку отдают (`hooks/use-visited.ts:15,32`, `hooks/use-places.ts:22`);
в `frontend/src/components/map/index.tsx` ARIA — это `role="status"` на оверлее
(`:929`) и `aria-hidden` на иконке стрелки (`:729`): роль одна, атрибутов два.

**Что сделать.** Включить `control-has-associated-label`, `button-has-type`,
`anchor-is-valid`, `click-events-have-key-events`, `no-static-element-interactions`,
`label-has-associated-control` **и починить все попадания тем же коммитом** (линт — гейт,
поэтому включение отдельно его уронит). Проставить `aria-label` двум иконочным кнопкам
без имени в `frontend/src/components/admin/places-panel.tsx` — корзине (`:343-353`) и
крестику (`:430-432`). Показать `visited.error` и добавить `onError` переключателям в
`visited/visited-page.tsx`. Дать карте роль и имя. Escape + возврат фокуса + инертный
фон для полноэкранного
`frontend/src/components/parts/ferrostar-navigation-hud.tsx` (имя у секции уже есть,
`aria-label` на `:115`; `aria-hidden` стоит на декоративных иконках — не хватает
именно клавиши и фокуса). `visualViewport` в
`frontend/src/components/mobile/use-mobile-sheet-snap.ts`, чтобы клавиатура не накрывала
поле открытой шторки.

## 8. C7 — Токены, `ToolButton`, inline-подтверждения `[B]`

`ToolButton` рисует себя хексами мимо дизайн-системы, тона вердикта и предупреждений
собраны из сырой палитры Tailwind, а подтверждения спрашиваются нативным
`window.confirm`.

Проверил: в `frontend/src/components/map/parts/tool-button.tsx` цвета вписаны в `style`
(`#ff385c`, `#ffe4ea`, `#ffffff`) и фокус — `outline-[#ff385c]`; токенов
`--warning`/`--success`/`--info` в `frontend/src/index.css` нет, а сырая палитра живёт в
`plan-verdict.tsx:97`, `sidebar.tsx:1158,1169,1404,1700,1743`, `guide-panel.tsx:309`,
`waypoint-list.tsx:173`; `window.confirm` — в `admin/places-panel.tsx:88` и
`admin/users-panel.tsx:45`.

**Что сделать.** Перевести `tool-button.tsx` на токены. Завести
`--warning`/`--success`/`--info` и заменить сырые утилиты. Заменить `window.confirm` на
inline-подтверждение (кнопка → «удалить?» → подтвердить/отменить), переиспользуемое в
обоих местах: образец из исходного плана (`components/account/delete-my-data.tsx`)
удалён вместе с фичей, шаблон надо сделать заново. Свериться с `frontend/DESIGN.md`:
форкать `components/ui/*` по-прежнему нельзя.

Предусловия «C2» у пункта нет: блок токенов — это и есть работа выше, а не то, что
должно было случиться до неё.

## 9. C8 — Производительность `[B]`

`frontend/nginx.conf` не сжимает и не кэширует ничего — проверил, `rg` по
`gzip|immutable|Cache-Control|expires` пуст. Ни один роут не ленив:
`frontend/src/routes.tsx` импортирует `app`/`admin`/`login`/`register`/`visited`
статически, `lazy`/`lazyRouteComponent` в нём нет. `frontend/vite.config.ts` не содержит
`build.rollupOptions`. `frontend/src/lib/ferrostar-nav.ts:40` зовёт
`export const ferrostarReady = loadFerrostar()` на импорте модуля, а `guide-panel`
импортирует его статически — WASM Ferrostar скачивается всеми, включая тех, кто ни разу
не запускает навигацию (единственный читатель — `guide-panel.tsx:790`).

**Что сделать.**

* **nginx** — включить gzip/brotli, `immutable` для `/assets`, no-cache для `index.html`
  и `sw.js`. Самый крупный выигрыш без правок кода.
* **Роуты** — `lazyRouteComponent` для login/register/admin/visited + `Suspense`/
  `pendingComponent`; в том же коммите перевести `frontend/src/app.spec.tsx` на `findBy*`
  (асинхронные компоненты роутов) — сейчас там только синхронные `screen.getByTestId`.
* **Vendor-чанки** — `build.rollupOptions.output.manualChunks` для react/tanstack/i18n/zod,
  исключив `maplibre-gl` и `@stadiamaps/ferrostar`.
* **Ferrostar** — ленивая загрузка по намерению вместо `ferrostarReady` на импорте.
* **Карта** — `services-layer.tsx:208` зовёт `visibleServices(...)` (внутри `sort` на
  `:135`) в рендере, а `:185` дёргает `setCentre` на каждое движение карты;
  `places-layer.tsx:74-75` пересобирает paint-объект каждый рендер; стабилизировать
  пустой дефолт — свежий `[]` рождается в `hooks/use-places.ts:18`
  (`query.data?.items ?? []`), вызов на `map/index.tsx:99`. `route-lines.tsx:55` не
  трогаем: там уже `useMemo`.
* **Полезная нагрузка** — `GET /places` отдаёт до `MAX_PLACES = 5000`
  (`backend/db/store/places.py:36`) без сжатия. После gzip — померить; если мало, сузить
  набор полей ответа.

## 10. C9 — Дедупликация утилит и API `[M]`

Расстояние считается в четырёх файлах — и **четырьмя разными формулами**, так что
это не копии; каждый модуль `src/api/*` сам собирает `AGENT_URL` и заголовки.

Проверил: `metresBetween` определён в `src/components/parts/guide-format.ts:43`
(равнопромежуточная на `111_320`) и `src/components/map/parts/route-walk.ts:14`
(равнопромежуточная на `EARTH_RADIUS_M`), в
`src/components/map/parts/services-layer.tsx:90` — **haversine**, плюс плоская
`metres` в `src/lib/geo-sim.ts:42`. `AGENT_URL` задублирован в шести местах:
`src/api/services.ts:7`, `src/api/progress.ts:27`, `src/api/account.ts:20`,
`src/api/itineraries.ts:6`, `src/api/places.ts:6`, `src/components/sidebar.tsx:90`;
файла `src/api/request.ts` нет.

**Что сделать.** Свести расстояние в `src/utils/geo.ts`, **выбрав одну из четырёх
формул осознанно** — числа сдвинутся, это не переименование. Завести общий
`src/api/request.ts` (`AGENT_URL` + `request<T>`) и перевести на него `src/api/*`;
заготовка уже есть — локальный `request<T>` в `src/api/account.ts:82-94`.
Форматтеры дат сюда больше не входят — `src/i18n/format.ts` уже есть, и других
`toLocale*` в дереве не осталось (сделано в C4). Пункт про devtools отпал:
`root-component.tsx` на месте (`src/components/root-component.tsx:1`), но `devtools`
в нём не смонтированы, а `devtools(...)` — это middleware zustand
(`stores/common-store.ts:95`, `stores/directions-store.ts:243`) без отдельных
зависимостей, так что удалять нечего.

Проверка всех четырёх: `make test-frontend`; для C6 — `/run` с axe/скринридером; для
C7 — визуальная сверка тонов и inline-подтверждений; для C8 — размер начального чанка и
ответ `Content-Encoding: gzip` на `/assets/*`.
