# Интеграция Ferrostar в гид-навигатор (спек для агента)

Рабочая директория: /workspaces/demo/frontend
Пакеты уже установлены: `@stadiamaps/ferrostar` (WASM-ядро, Rust), `@stadiamaps/ferrostar-webcomponents`.
Vite-плагины `vite-plugin-wasm` + `vite-plugin-top-level-await` уже подключены в vite.config.ts, build проходит.

## Цель
Заменить самописный «движок» навигатора на Ferrostar NavigationSession, СОХРАНИВ нашу карту (react-map-gl в src/components/map/index.tsx). HUD навигатора строится поверх карты из виджетов `instructions-view` и `trip-progress-view` пакета `@stadiamaps/ferrostar-webcomponents`; web-компонент `<ferrostar-map>` НЕ используем. Наши карточки остановок, действия гида и список маршрута остаются доступны из HUD.

## Что Ferrostar берёт на себя (вместо текущего самописного кода)
- снап GPS-фикса к линии маршрута (`snappedUserLocation`)
- курс движения (`courseOverGround` со SnapToRoute-фильтрацией)
- прогресс: distanceToNextManeuver, distanceRemaining
- детекция схода с маршрута (RouteDeviationTracking StaticThreshold)
- выбор активной озвучки (spokenInstruction по triggerDistanceBeforeManeuver)
- переход на следующий манёвр (stepAdvanceCondition DistanceEntryExit)

## Что остаётся нашим
- карта, камера (follow/bearing/zoom-эффекты в map/index.tsx — не трогать, только источник course изменится: вместо guideFix.course из guide-course.ts брать courseOverGround от снапнутого состояния)
- UI guide-panel.tsx (карточки, режимы review/moving)
- геолокация: текущий watchPosition в guide-panel остаётся источником фиксов, но каждый фикс кормим в Ferrostar
- режим симуляции ?sim=walk (geo-sim.ts) — фикс по-прежнему симулируется и кормится в Ferrostar так же

## Архитектура нового модуля
Создай `src/lib/ferrostar-nav.ts` (и `src/lib/ferrostar-nav.spec.ts`):

1. `buildFerrostarRoute(routeData: ParsedDirectionsGeometry, fallbackPoints?)` — собирает Ferrostar `Route` из наших Valhalla-данных (тип уже есть, см. src/api/types.ts и usage buildManeuvers в guide-panel.tsx):
   - geometry: все точки линии (buildLine в guide-panel.tsx строит line.points; можно переиспользовать/вынести buildLine в ferrostar-nav или дублировать расчёт cum-дистанций)
   - steps: по каждому манёвру каждого leg: geometry = срез shape от begin_shape_index до следующего манёвра, distance = манёвр `length` (м), duration = манёвр `time`, instruction = манёвр `instruction`, roadName = mnv.street_name если есть
   - spokenInstructions на каждый step: сгенерируй на trigger-дистанциях [400, 200, 50, 0] запись { text: instruction манёвра, ssml: undefined, triggerDistanceBeforeManeuver: X, utteranceId: `${stepKey}-${X}` } — НО text для 400/200 добавляй с фразой «через X метров»? НЕТ: Ferrostar возвращает только сам text; языковую обёртку «через X метров, …» делаем в UI-слое как сейчас (см. ниже — voice-эффект остаётся наш, Ferrostar даёт только факт «пора объявлять манёвр N с дистанцией X»)
   - waypoints: первая точка = start (kind Break), последняя = Destination, промежуточные остановки маршрута (stops) как Break
   - верни также обратную карту stepKey -> index для UI

2. Класс `FerrostarNavigator` (обёртка над `NavigationSession` из '@stadiamaps/ferrostar'):
   - конструктор(route: Route)
   - `start(config?)`: создаёт NavigationSession c конфигом:
     stepAdvanceCondition: DistanceEntryExit { minimumHorizontalAccuracy: 25, distanceToEndOfStep: 30, distanceAfterEndStep: 5 }
     arrivalStepAdvanceCondition: DistanceToEndOfStep { distance: 30, minimumHorizontalAccuracy: 25 }
     routeDeviationTracking: StaticThreshold { minimumHorizontalAccuracy: 25, maxAcceptableDeviation: 25 }
     snappedLocationCourseFiltering: "SnapToRoute"
     waypointAdvance: WaypointWithinRange: 40 (совпадает с ARRIVAL_RADIUS_M в guide-panel)
   - `update(location: Fix): TripState | null` — превращает наш Fix {lat, lon, accuracy, at} в Ferrostar UserLocation (coordinates {lat,lng}, horizontalAccuracy, courseOverGround undefined, timestamp {secs_since_epoch: Math.floor(at/1000), nanos_since_epoch: 0}) и вызывает session.updateUserLocation; возвращает полученный TripState (type это tagged union { Idle | Navigating | Complete })
   - `state: TripState | null` — последний TripState
   - Методы диспоузить аккуратно (WASM): не вызывать session.free() пока живы ссылки.

3. Импорт WASM: в ferrostar-nav.ts сверху `import init from '@stadiamaps/ferrostar'`? Проверь фактический экспорт пакета (ferrostar.js — esm-инициализация wasm). Верная схема обычно:
   ```ts
   import init, { NavigationSession } from '@stadiamaps/ferrostar';
   await init(); // один раз, кешируй промис в module-level let
   ```
   Сделай `export const ferrostarReady = init-промис` и в update() ждём его (или класс async-фабрика `createFerrostarNavigator(route)`). Не оставляй top-level await вне функции.

## Изменения в guide-panel.tsx
Минимальные, только движок:
- удалить импорты courseAlongLine/courseAtPoint (parts/guide-course) и mergeMicroManeuvers/buildManeuvers-usage в той мере, в которой их заменяет Ferrostar; файлы parts/guide-course.ts и parts/guide-maneuvers.ts удали, ЕСЛИ они больше нигде не используются (grep). Их спеки тоже удали.
- эффект фикс-лупа: при каждом fix вызывай navigator.update(fix) (создавай navigator при переходе mode==='moving' и наличии line/routeData; пересоздавай при смене routeData/key)
- из TripState.Navigating публикуй в common-store:
  - setGuideFix({lat,lng,heading: null, course: courseOverGround?.degrees ?? null, at}) — но пуликуй СНАПНУТУЮ позицию? НЕТ: карта должна показывать реальную позицию туриста (у нас свой слой), снапнутую позицию пуликуй отдельным полем если потребуется позже. Пока: lat/lng = реальный фикс, course = Ferrostar courseOverGround (это и есть «доворачивание по участку маршрута»)
  - setGuideTurnDistanceM(округлённый progress.distanceToNextManeuver /10*10)
- offRoute: вместо самописного offRouteFixesRef-эффекта используй TripState.Navigating.deviation: CompletelyOffRoute → setOffRoute(true) (с гистерезисом 2 подряд состояния для шумоподавления — сохрани логику похожей), OffStepOnRoute → false
- traveled: замените самописный locateOnLine-progress на (общая длина - progress.distanceRemaining); back-jump защиту оставь (Math.max)
- voice: Ferrostar TripState.Navigating.spokenInstruction — используй его ПОЯВЛЕНИЕ/смену utteranceId как триггер; текст фразы собирай как сейчас: t('guide.voiceDistance', {distance: spokenInstruction.triggerDistanceBeforeManeuver, instruction: spokenInstruction.text}) — НЕ используй их ssml (не локализовано). mute/offRoute-логика decideVoice остаётся, но дистанция-триггер теперь от Ferrostar (spokenInstruction сменился). Если spokenInstruction undefined — ничего не говорим.
- auto-complete стопов (эффект с metresBetween/ARRIVAL_RADIUS_M) оставь как есть — Ferrostar waypointAdvance не трогает наш visited-механизм.
- активный манёвр для баннера: remainingSteps[0] (instruction/roadName) + progress.distanceToNextManeuver; иконка через getManeuverIcon — Ferrostar даёт visualInstruction с ManeuverType, СНАЧАЛА проверь соответствие: если легко замапить — используй visualInstruction.type, иначе оставь наш getManeuverIcon по первой строке instruction. Выбери простое.

## Тесты
- ferrostar-nav.spec.ts: buildFerrostarRoute строит шаги из фейкового Valhalla-ответа (2 leg'а, по 2 манёвра); navigator.update двигает состояние на фейковых фиксах вдоль линии (можно взять геометрию из фикстуры — прямая линия из 3 точек по 100 м) и проверяет: Navigating state, distanceToNextManeuver убывает, Complete при конце, deviation при фиксах в 100 м в сторону. WASM в vitest (jsdom, node) — если init не поднимается в jsdom, ограничь спек buildFerrostarRoute (чистая функция) и пометь навигационные тесты как integration-блокировку с комментарием; сообщи об этом честно в отчёте.
- НЕ трогай существующие спеки guide-panel.spec.tsx как_can: если после рефакторинга часть их падает по причине замены курса/прогресса на Ferrostar — обнови правдоподобные ассерты, но сохраняй инварианты честности (simulated badge, precise gating). Запусти `npx vitest run src/components/guide-panel.spec.tsx src/lib/ferrostar-nav.spec.ts src/stores` в конце.
- В конце: `npx tsc --noEmit` и `npm run build` — ОБА должны пройти (build нужен: WASM-плагины). Не запускай lint по всей базе.

## Запрещено
- не редактируй src/components/map/index.tsx (кроме ОДНОЙ строки: источник course, если map читает course из guideFix — он уже читает guideFix.course, так что менять нечего; проверь и не трогай)
- не трогай бэкенд, docker, nginx, i18n-словари (если нужны новые ключи guide.* — добавь в ОБОИХ src/i18n/namespaces/guide.ts ru+en и напиши об этом)
- не коммить

## Отчёт
Список файлов, что удалено/добавлено, результат tsc/vitest/build, что НЕ проверено.
