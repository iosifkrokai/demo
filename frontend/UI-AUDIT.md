Агент завершили аудит. Консолидирую результаты.

---

## Аудит UI/UX багов — valhalla-web

---

### P0 — Критические (данные/безопасность)

**1. `src/components/settings-panel/settings-options.ts:146–170` — Два UI-контрола перезаписывают один store-ключ**

```ts
const turnPenaltyCost = { param: 'maneuver_penalty', ... };  // line 146
const maneuverPenalty = { param: 'maneuver_penalty', ... };  // line 159
```

`turnPenaltyCost` и `maneuverPenalty` — два отдельных слайдера с одинаковым `param: 'maneuver_penalty'`. При сборке store в объект последний дублирующий ключ перезаписывает первый (last-write-wins). Для профилей `truck` и `motorcycle` оба слайдера отображаются, но реально работает только последний. Юзер, двигающий "Turn Penalty" (max 20), на самом деле пишет в тот же ключ, что и "Maneuver Penalty" (max 60).

**Как чинить:** Переименовать один из `param` (напр. `turn_penalty`) или убрать дубликат.

---

### P1 — Высокий приоритет

**2. `src/components/mobile/mobile-shell.tsx:108–111` — CSS-переменная `--sheet-h` не очищается при unmount/remount**

```ts
useEffect(() => {
  const root = document.documentElement;
  return () => root.style.setProperty('--sheet-h', '0px');  // ← setProperty('0px'), не removeProperty
}, []);
```

Используется `setProperty('--sheet-h', '0px')`, а не `removeProperty`. При быстром remount (StrictMode double-mount, fast route change) cleanup может отработать после mount-эффекта следующего рендера, оставив `--sheet-h` в устаревшем значении. Все floating-контролы на карте (кнопка открытия, place card) позиционируются через `--sheet-h` — в итоге они оказываются в неправильном месте или перекрывают друг друга.

**Как чинить:** Заменить на `removeProperty('--sheet-h')` в cleanup, или использовать shared `isMounted` ref.

---

**3. `src/components/mobile/use-mobile-sheet-snap.ts:235` — `setPointerCapture` без `releasePointerCapture`**

```ts
const onPointerDown = useCallback((event) => {
  event.currentTarget?.setPointerCapture?.(event.pointerId);  // ← захвачен
  ...
}, []);
// releasePointerCapture никогда не вызывается
```

Если `pointercancel` происходит (системный диалог, потеря фокуса таба), pointer остаётся захваченным. Любой последующий `pointermove`/`pointerup` роутится к этому элементу, даже если драг был отменён.

**Как чинить:** Сохранять `releaseCapture` в gesture ref и вызывать в `onPointerCancel`.

---

**4. `src/components/mobile/place-card-body.tsx:233,250,277,295` — Элементы массивов за keyed по индексу**

```tsx
{details.funFacts.slice(0, 3).map((fact, i) => (  // line 233
  <div key={i} ...>
{details.links.slice(0, 4).map((link, i) => (      // line 250
  <a key={i} ...>
```

Два отдельных списка с `key={i}` из одного и того же `details` объекта. При reorder/replace массива (обновление `details` с сервера) React десинхронизирует DOM-узлы с данными — `:visited` стили применяются к неправильным ссылкам, анимации ломаются.

**Как чинить:** Использовать стабильный ключ: `key={`fact-${fact.slice(0, 20)}`}` или `key={link.url}`.

---

**5. `src/components/mobile/place-card-body.tsx:117–121` — `photo` prop может быть `undefined`**

```tsx
<PlacePhoto
  photo={details.photo}  // может быть undefined
  name={details.name}
  className="mt-0.5"
/>
```

`details.photo` типизирован как опциональный. Если `PlacePhoto` не защищён внутри — рендер-краш. Если защищён — пустое место резервируется в карточке с `max-h-[52dvh]`, сжимая остальной контент.

**Как чинить:** Обернуть в `{details.photo && <PlacePhoto .../>}`.

---

**6. `src/components/mobile/mobile-section.tsx:41` — `useId()` создаёт нестабильный `aria-controls`**

```ts
const panelId = `${useId()}-${id}`;  // useId() возвращает новое значение на КАЖДЫЙ рендер
```

`useId()` генерирует новый ID при каждом рендере. `aria-controls={panelId}` на кнопке-тейгле указывает на ID, который меняется при каждом перерендере. WCAG требует стабильного `id` для `aria-controls`.

**Как чинить:** Использовать только проп `id`: `const panelId = \`section-panel-${id}\``.

---

**7. `src/components/route-planner.tsx:52` — `activeTab` не валидируется, `tabConfig` может быть `undefined`**

```ts
const tabConfig = TAB_CONFIG[activeTab as keyof typeof TAB_CONFIG];
// если activeTab не в TAB_CONFIG → undefined → краш при чтении tabConfig.xxx
```

Прямой доступ по ключу без fallback. Старый bookmark URL или манипуляция с router produceют runtime TypeError.

**Как чинить:** `const tabConfig = TAB_CONFIG[activeTab as keyof typeof TAB_CONFIG] ?? TAB_CONFIG.directions`.

---

**8. `src/components/route-planner.tsx:58–75` — Двойной refetch + 1-секундный `setTimeout` на каждую смену профиля**

```ts
const handleProfileChange = (value: Profile) => {
  navigate(...);
  if (activeTab === 'isochrones') {
    refetchIsochrones();
    setTimeout(() => { refetchDirections(); }, 1000);  // ← race condition
  } else {
    refetchDirections();
    setTimeout(() => { refetchIsochrones(); }, 1000);
  }
};
```

Обе ветки безусловно запускают активный refetch и отложённый refetch неактивной вкладки. Если пользователь быстро переключает табы, накапливаются orphan-запросы. Magic number 1000ms — не синхронизируется с жизненным циклом данных.

**Как чинить:** Refetch только активный таб. Кросс-таб кэширование — на уровне hook, не таймера.

---

**9. `src/components/sidebar.tsx:2311,2328` — Кнопки `-`/`+` счётчика группы 28×28px на десктопе**

```tsx
<button className="flex h-7 w-7 items-center justify-center rounded-full ...">  // 28px
<button className="flex h-7 w-7 items-center justify-center rounded-full ...">  // 28px
```

`h-7 w-7` = 28×28px. Mobile override (`max-md:h-11`) поднимает до 44px только на мобильных. На десктопе кнопки не проходят WCAG SC 2.5.8 (44×44px) и неудобны для fat-finger users.

**Как чинить:** `h-9 w-9 max-md:h-11 max-md:w-11`.

---

**10. `src/components/guide-panel.tsx:921–946` — `reroute` диспатчит потенциально stale координаты**

```ts
const reroute = useCallback(() => {
  const store = useDirectionsStore.getState();
  if (fix) {  // ← fix захвачен при создании callback, не при вызове
    window.dispatchEvent(new CustomEvent('grodno:guide-reroute', {
      detail: { lat: fix.lat, lon: fix.lon }  // ← stale через 1 render cycle
    }));
  }
}, [onReroute, fix, t]);
```

`fix` в deps, callback пересоздаётся при изменении `fix`. Но диспатч синхронно читает `fix` из closure. Если новый GPS-fix прибывает между рендером и вызовом `reroute()`, диспатчится устаревшая позиция.

**Как чинить:** Читать `useDirectionsStore.getState().guideFix` в момент вызова, не в момент создания callback.

---

**11. `src/components/map/parts/services-layer.tsx:334–348` — Service-маркер touch target 24–36px**

```tsx
<button className="h-6 w-6 ...">  // 24px, pointer-coarse → 36px
```

Не проходит WCAG 2.5.5 minimum 44×44px. Мисклик регистрируется как map click на карту.

**Как чинить:** `h-9 w-9 pointer-coarse:h-11 pointer-coarse:w-11`.

---

**12. `src/components/map/parts/map-context-menu.tsx:29–51` — Кнопки контекстного меню < 44px**

```tsx
<ButtonGroup>
  <Button size="sm" ...>   // shadcn sm ≈ 32px
  <Button size="sm" ...>   // shadcn sm ≈ 32px
  <Button size="sm" ...>   // shadcn sm ≈ 32px
</ButtonGroup>
```

Меню вызывается long-press (100ms) на мобильных. Экшны "Directions from here", "Add as via point", "Directions to here" — все 32px.

**Как чинить:** `size="default"` или `className="min-h-11"`.

---

**13. `src/components/map/index.tsx` (guiding entry) — Нет обработки ошибок геолокации**

```ts
// При входе в guiding mode:
setFollow(true);  //无条件
// Если geolocation denied → guideFix остаётся null
// Tourist видит всю UI "navigation mode" но position never updates
```

Нет guard: если `guideFix` не появляется в течение timeout (3s), пользователь получает broken navigation UI без какого-либо feedback.

**Как чинить:** Добавить toast "Location access required" если `guideFix` не установлен в течение 3s.

---

**14. `src/hooks/use-directions-queries.ts:358–398` — TanStack Query race condition**

```ts
const { refetch: refetchDirections } = useDirectionsQuery();  // enabled: false
// При drag waypoint → refetch
updateWaypointPosition(...).then(() => { refetchDirections(); });  // line 253
```

Query key — `['directions']` без waypoints/settings/profile. Быстрые successive drags запускают несколько `refetch()` одновременно. TanStack возвращает первый resolved результат, не последний. Если запросы приходят out-of-order, store получает stale данные.

**Как чинить:** Добавить waypoints/settings/profile в query key для auto-invalidation, или использовать `AbortController` для cancel previous requests.

---

**15. `src/hooks/use-isochrones-queries.ts:26–76` — Isochrone fetch нельзя отменить**

```ts
async function fetchIsochrones() {
  const response = await fetch(...);  // без AbortController
}
```

Быстрые successive запросы race. Если старый запрос приходит после нового, stale polygons перезаписывают свежие, а `successful: true` флаг остаётся от старого запроса.

**Как чинить:** Использовать `AbortController` в ref, abort previous request перед новым. Или перейти на mutation с `onMutate`/`onSettled`.

---

**16. `src/components/ui/slider-setting.tsx:94`, `select-setting.tsx:56`, `checkbox-setting.tsx:39` — Help-иконки 24×24px**

```tsx
<Button type="button" variant="ghost" size="icon-xs" ...>  // size-6 = 24×24px
```

Каждая строка настроек имеет help-кнопку 24px. WCAG 2.1 SC 2.5.8 (AA): минимум 44×44px. На мобильных эти кнопки невозможно надёжно открыть.

**Как чинить:** Заменить на `size="icon"` (36px) + `className="min-w-[44px] min-h-[44px]"`.

---

**17. `src/components/quick-settings.tsx:214–221` — Смена языка не триггерит refetch изохрон**

```ts
const handleLanguageChange = useCallback(
  (value: string) => {
    setDirectionsLanguage(newLanguage);
    setLanguage(newLanguage);
    refetchDirections();  // ← только directions
    // refetchIsochrones отсутствует!
  },
  [refetchDirections]  // ← refetchIsochrones тоже нет в deps
);
```

Если активен таб `isochrones`, смена языка в quick-settings обновляет store, URL, но не перезапрашивает isochrone данные. Turn-by-turn instructions на изохронах рендерятся в старом языке.

**Как чинить:** Добавить `refetchIsochrones` в deps и вызывать оба refetch.

---

**18. `src/components/settings-panel/settings-panel.tsx:76–85` — Clipboard write без try/catch**

```ts
await navigator.clipboard.writeText(text);  // может reject (HTTPS required, permission denied)
setCopied(true);  // не reached если reject
```

`navigator.clipboard` требует secure context. При permission denial пользователь видит: ничего. Операция тихо фейлится.

**Как чинить:** Обернуть в try/catch, показывать toast error или inline feedback при фейле.

---

**19. `src/utils/filter-profile-settings.ts:41–92` — Нет валидации профиля на runtime**

```ts
for (const setting in settings) {
  if (profile in generalSettings) { ... }
  if (profile in profileSettings) { ... }
}
// Несуществующий профиль → пустой {} без ошибки
```

Если `profile` не найден в `generalSettings`/`profileSettings`, функция возвращает пустой объект. `handleCopySettings` пишет пустую JSON в clipboard — пользователь получает `{}` без какого-либо feedback.

**Как чинить:** Явно проверять и бросать ошибку с понятным сообщением наверх.

---

### P2 — Средний приоритет

**20. `src/components/mobile/mobile-section.tsx:61` — Toggle button `min-h-9` (36px) < 44px**  
→ `max-md:min-h-11`

**21. `src/components/mobile/place-card-body.tsx:97–99` — `details.name` без null-guard/fallback**  
→ `{details.name ?? t('map.placeNoName')}`

**22. `src/components/mobile/place-card-body.tsx:251–260` — External links без `aria-label` для new-tab**  
→ `aria-label={\`${link.title} (${t('a11y.opensInNewTab')})\`}`

**23. `src/components/mobile/mobile-shell.tsx:94–98` — `wasOpen` ref stale при смене `initialSnap`**  
→ Инициализировать `useRef(false)` и добавить `initialSnap` в deps

**24. `src/components/mobile/mobile-shell.tsx:131–133` — `navigate()` без error handling**  
→ `navigate(...).catch(console.error)`

**25. `src/components/mobile/use-mobile-sheet-snap.ts:220–223` — Effect без dep array синхронит `latest` ref**  
→ Добавить `[snap, restingPx, onDismiss]`

**26. `src/components/mobile/use-mobile-sheet-snap.ts:286` — `onDismiss` отсутствует в deps `release` callback**  
→ Добавить `onDismiss` в dep array

**27. `src/components/mobile/use-mobile-sheet-snap.ts:296–302` — `reduce` без initial value — time bomb**  
→ Передать `ORDER[ORDER.length - 1] ?? 'full'` как initial

**28. `src/components/sidebar.tsx:644–704` — `t` отсутствует в deps `locateMe`**  
→ Добавить `t` (latent, harmless today)

**29. `src/components/sidebar.tsx:802–852` — Voice language игнорирует `i18n`/`t` changes**  
→ Добавить `t, i18n` в deps useEffect

**30. `src/components/sidebar.tsx:694–701` — Geolocation error handler discarding error object**  
→ `console.warn('[locateMe] geolocation error:', err)`

**31. `src/components/guide-panel.tsx:802–811` — `prevManeuverRef` races с StrictMode double-invocation**  
→ Вынести ref-sync в отдельный effect, keyed only on `activeManeuver`

**32. `src/components/guide-panel.tsx:581–621 + 783–799` — Два эффекта перезаписывают `setGuideFix`; второй безусловно wins**  
→ Объединить в один effect, вычисляющий оба значения

**33. `src/components/map/index.tsx:372–446` — Route zoom effect не null-guards `mapRef.current` до `fitBounds`**  
→ `if (!mapRef.current) return` в начале effect body

**34. `src/components/map/index.tsx:597–607` — `panAwayRef` не сбрасывается при смене таба**  
→ `idleMs = Date.now() - panAwayRef.current` может быть negative/ enormous после tab switch

**35. `src/components/map/parts/tiles-info-popup.tsx:17–24` — Close button `position: absolute` без `position: relative` на контейнере**  
→ Добавить `relative` родителю и `z-10` кнопке

**36. `src/stores/directions-store.ts:625–644` — `excludeStops` не вызывает `snapshotRoute()` перед splice**  
→ Undo не может восстановить удалённые waypoints

**37. `src/stores/isochrones-store.ts:65–76` — `clearIsos` не сбрасывает `successful` перед новым fetch**  
→ Stale polygons могут остаться на экране при race condition

**38. `src/hooks/use-isochrones-queries.ts:118–132` — `fetchReverseGeocode` без user-facing toast на error**  
→ Добавить `toast.error()` в catch

**39. `src/hooks/use-directions-queries.ts:207–218` — Non-154 errors показывают generic message, нет retry logic**  
→ 401/500 от Valhalla неотличимы от "route too long"

**40. `src/components/map/parts/services-layer.tsx:91–119` — Module-level `lastBoxes` shared между всеми инстансами**  
→ Вынести в `useRef` inside hook

**41. `src/components/settings-panel/settings-panel.tsx:103` — PopoverContent внутри Sheet может overflow sheet bottom**  
→ `className="max-h-[300px] overflow-auto"` для PopoverContent внутри sheet

**42. `src/components/quick-settings.tsx:134–154` — URL hydration effect с `eslint-disable-next-line react-hooks/exhaustive-deps`**  
→ Убрать suppress и добавить `[search, updateSettings, navigate]` в deps

**43. `src/components/ui/slider-setting.tsx:55–66` — NaN от `parseFloat` не обрабатывается during typing**  
→ `"5."` ресетится к `min` на blur — неожиданно для юзера

**44. `src/components/settings-panel/settings-panel.tsx:87–94` — `resetConfigSettings` always refetch даже если change не было**  
→ Добавить diff check перед refetch

---

### Сводная таблица

| # | Файл | Строка | Проблема | P |
|---|------|--------|----------|---|
| 1 | settings-options.ts | 146–170 | Duplicate `maneuver_penalty` param → data corruption | **P0** |
| 2 | mobile-shell.tsx | 108–111 | `--sheet-h` cleanup race | P1 |
| 3 | use-mobile-sheet-snap.ts | 235 | PointerCapture leak | P1 |
| 4 | place-card-body.tsx | 233,250,277,295 | Array keys by index | P1 |
| 5 | place-card-body.tsx | 117–121 | Undefined `photo` | P1 |
| 6 | mobile-section.tsx | 41 | Unstable `aria-controls` via `useId()` | P1 |
| 7 | route-planner.tsx | 52 | Unvalidated `activeTab` → undefined crash | P1 |
| 8 | route-planner.tsx | 58–75 | Double refetch + magic timeout | P1 |
| 9 | sidebar.tsx | 2311,2328 | Stepper buttons 28×28px | P1 |
| 10 | guide-panel.tsx | 921–946 | Stale `fix` in reroute event | P1 |
| 11 | services-layer.tsx | 334–348 | Service marker < 44px | P1 |
| 12 | map-context-menu.tsx | 29–51 | Context menu buttons < 44px | P1 |
| 13 | map/index.tsx | guiding entry | No geolocation error handling | P1 |
| 14 | use-directions-queries.ts | 358–398 | TanStack Query race on refetch | P1 |
| 15 | use-isochrones-queries.ts | 26–76 | Uncancellable isochrone fetch | P1 |
| 16 | slider/select/checkbox-setting.tsx | various | Help icons 24×24px | P1 |
| 17 | quick-settings.tsx | 214–221 | Language change skips isochrones refetch | P1 |
| 18 | settings-panel.tsx | 76–85 | Clipboard write without try/catch | P1 |
| 19 | filter-profile-settings.ts | 41–92 | No runtime profile validation | P1 |
| 20–44 | (см. выше) | various | 25 дополнительных P2 | P2 |

**Итого: 1 P0, 18 P1, 25 P2. Главный P0 — duplicate `maneuver_penalty` в settings-options.ts, строки 146 и 159, где два слайдера перезаписывают один store-ключ.**
