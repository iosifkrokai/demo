/**
 * One locale area lives in one file: `ru` and `en` side by side.
 *
 * The point is ownership. A single shared dictionary meant two people editing
 * the same two files for unrelated screens; an area file plus the components of
 * that area is a self-contained piece of work. `src/i18n/index.ts` merges these
 * into the resources i18next gets, and a test asserts `ru`/`en` stay in step —
 * a missing translation is a failing test, not a raw key on screen.
 */
export interface LocaleArea<T = Record<string, unknown>> {
  ru: T;
  en: T;
}

/**
 * Keys of the navigator («Проводник»): stop states, progress, turn phrasing.
 *
 * This area owns everything the guide says. The panel (`guide-panel.tsx`) and
 * its parts (stop card, stop list, progress, route-done, empty) read their text
 * from here, and `parts/guide-mode.ts` / `parts/guide-format.ts` — which are
 * plain modules, not components — pull their words through the same i18n
 * instance, so nothing user-facing is hard-coded in Russian.
 *
 * The keys below also cover the two things the *sidebar* says about the guide
 * («Пойти по маршруту», «выйти», the «Проводник» title and the counted
 * «остановки»): the area file is merged over the core dictionary by key, so it
 * has to keep them, or the merge would drop the sidebar's copy.
 *
 * Plurals follow i18next: a counted noun gets `_one/_few/_many` in Russian and
 * `_one/_other` in English. The parity test checks both sets.
 */
export const guideArea = {
  ru: {
    guide: {
      // ── Panel head ───────────────────────────────────────────────────────
      title: 'Проводник',
      subtitle: 'идём по маршруту остановка за остановкой',
      reset: 'сбросить',
      resetTitle: 'начать маршрут заново',
      soundOn: 'звук вкл',
      soundOff: 'звук выкл',
      enableSound: 'включить звук',
      disableSound: 'выключить звук',

      // ── Entering and leaving the guide (sidebar) ─────────────────────────
      enter: 'Пойти по маршруту',
      enterHint: 'Проводник отметит пройденное и подскажет повороты',
      exit: 'выйти',

      // ── Route actions ────────────────────────────────────────────────────
      start: 'начать маршрут',
      overview: 'обзор',
      advance: 'я на месте',
      finish: 'завершить',
      again: 'пройти заново',
      nextStop: 'следующая остановка',
      goToStop: '{{imperative}} к остановке «{{name}}»',
      followRoute: '{{imperative}} по маршруту',

      // ── The big turn banner ──────────────────────────────────────────────
      maneuverIn: 'через {{distance}}',
      noSignal: 'сигнала нет — идите по линии маршрута',
      distanceHidden: 'расстояние скрыто: сигнал GPS неточный',
      announceIn: 'Через {{distance}}: {{instruction}}',

      // ── The quiet line that says where the position came from ────────────
      geoUnavailable: 'геолокация недоступна — отмечайте остановки вручную',
      geoLocating: 'определяю, где вы…',
      // Honest about a lost fix, and never a promise it cannot keep.
      geoStale: 'сигнал GPS потерян — отмечайте остановки вручную',
      geoPoor: 'GPS неточный (±{{metres}} м) — подсказки приблизительные',
      geoDistanceToNext: 'до следующей {{distance}}',
      geoOnRoute: 'вы на маршруте',

      // ── Off route ────────────────────────────────────────────────────────
      offRouteTitle: 'вы сошли с маршрута',
      offRouteAt: 'вы в ~{{distance}} от линии.',
      offRouteBody:
        'Перестроим от вас — остановки и обязательные точки сохранятся.',
      reroute: 'перестроить от меня',
      onRoute: 'я на маршруте',

      // ── Turn-by-turn banner (prominent display in moving mode) ───────────────
      turnInAhead: '{{distance}} — {{instruction}}',
      turnInAheadStandalone: 'через {{distance}}',
      // ── Nearby POI hints (navigator-style inline prompts) ──────────────────
      nearbyService: '{{name}} в {{distance}}',
      nearbyServiceOnRoute: '{{name}} по пути',
      nearbyServiceIfAlong: '{{name}} в {{distance}}, если по пути',
      nearbyToilet: 'туалет',
      nearbyCafe: 'кафе',
      nearbyRestaurant: 'ресторан',
      nearbyHotel: 'гостиница',
      nearbyBusStop: 'остановка',
      nearbyGeneric: 'место',

      // ── Suggestions along the way ────────────────────────────────────────
      suggestionsTitle: 'по пути — предложения, маршрут не меняют',
      suggestionAdd: 'добавить',
      suggestionSkip: 'не надо',

      // ── Voice ────────────────────────────────────────────────────────────
      voiceDistance: 'Через {{distance}} метров {{instruction}}',
      voiceArrived: 'Вы прибыли',
      continuingNavigation: 'Продолжаем навигацию…',

      // ── The «моё местоположение» start waypoint ──────────────────────────
      myLocation: 'Моё местоположение',
      routeStart: 'старт маршрута',

      // ── Progress ─────────────────────────────────────────────────────────
      alongLine: 'по линии',
      alongRoute: 'по маршруту',
      passed: 'пройдено',
      progressOf: '{{done}} из {{total}}',
      visitsLeft: 'осталось осмотра ~{{time}}',
      withTravelLeft: 'с дорогой осталось ~{{time}}',
      lineRemaining: 'осталось {{distance}}',
      progressLabel: 'прогресс маршрута',
      progressLine: '{{doneWord}} {{percent}}% линии',

      // ── Next-stop card ───────────────────────────────────────────────────
      distanceToStop: 'до неё {{distance}}',
      visitApprox: 'осмотр ≈ {{time}}',
      travelFor: '{{verb}} ~{{time}}',
      arrival: 'прибытие {{eta}}',
      openInMaps: 'открыть в картах',

      // ── Stop list ────────────────────────────────────────────────────────
      stopListTitle: 'остановки · {{visited}} из {{total}}',

      // ── Route done / nothing to walk ─────────────────────────────────────
      routeDoneTitle: 'маршрут пройден',
      routeDoneBody: 'все {{total}} остановок отмечены — можно начать заново',
      emptyTitle: 'маршрута пока нет',
      emptyBody:
        'Соберите маршрут в режиме планирования — и возвращайтесь сюда, чтобы идти по нему остановка за остановкой.',

      // ── How the guide talks about movement, per transport ────────────────
      modeFootVerb: 'идти',
      modeFootDone: 'пройдено',
      modeFootLabel: 'пешком',
      modeFootImperative: 'Идите',
      modeBikeVerb: 'ехать',
      modeBikeDone: 'проехано',
      modeBikeLabel: 'на велосипеде',
      modeBikeImperative: 'Поезжайте',
      modeCarVerb: 'ехать',
      modeCarDone: 'проехано',
      modeCarLabel: 'на машине',
      modeCarImperative: 'Поезжайте',
      modeCarHint: 'припаркуйтесь у остановки',

      // ── Units ────────────────────────────────────────────────────────────
      metres: '{{value}} м',
      km: '{{value}} км',
      minutes_one: '{{count}} мин',
      minutes_few: '{{count}} мин',
      minutes_many: '{{count}} мин',
      hours_one: '{{count}} ч',
      hours_few: '{{count}} ч',
      hours_many: '{{count}} ч',
      stops_one: '{{count}} остановка',
      stops_few: '{{count}} остановки',
      stops_many: '{{count}} остановок',

      // ── Honest wording for a failed /routes/generate request ─────────────
      agentNotFound:
        'агент не отвечает по этому адресу (404) — похоже, приложение обращается не к тому серверу. Это ошибка настройки, а не «ничего не найдено».',
      agentServerError: 'агент ответил ошибкой {{status}} — попробуйте ещё раз',
      agentDenied:
        'агент отклонил запрос ({{status}}) — проверьте доступ к сервису.',
      agentBadRequest:
        'агент не понял запрос ({{status}}) — переформулируйте, что хотите посмотреть.',
    },
  },
  en: {
    guide: {
      // ── Panel head ───────────────────────────────────────────────────────
      title: 'Guide',
      subtitle: 'walking the route stop by stop',
      reset: 'reset',
      resetTitle: 'start the route over',
      soundOn: 'sound on',
      soundOff: 'sound off',
      enableSound: 'turn the sound on',
      disableSound: 'turn the sound off',

      // ── Entering and leaving the guide (sidebar) ─────────────────────────
      enter: 'Walk the route',
      enterHint: 'The guide marks what you have walked and calls the turns',
      exit: 'exit',

      // ── Route actions ────────────────────────────────────────────────────
      start: 'start the route',
      overview: 'overview',
      advance: 'I am here',
      finish: 'finish',
      again: 'walk it again',
      nextStop: 'next stop',
      goToStop: '{{imperative}} to the stop “{{name}}”',
      followRoute: '{{imperative}} along the route',

      // ── The big turn banner ──────────────────────────────────────────────
      maneuverIn: 'in {{distance}}',
      noSignal: 'no signal — follow the route line',
      distanceHidden: 'distance hidden: the GPS fix is inaccurate',
      announceIn: 'In {{distance}}: {{instruction}}',

      // ── The quiet line that says where the position came from ────────────
      geoUnavailable: 'geolocation unavailable — mark the stops by hand',
      geoLocating: 'finding out where you are…',
      // Honest about a lost fix, and never a promise it cannot keep.
      geoStale: 'GPS signal lost — mark the stops by hand',
      geoPoor: 'GPS inaccurate (±{{metres}} m) — the prompts are approximate',
      geoDistanceToNext: '{{distance}} to the next stop',
      geoOnRoute: 'you are on the route',

      // ── Off route ────────────────────────────────────────────────────────
      offRouteTitle: 'you have left the route',
      offRouteAt: 'you are ~{{distance}} off the line.',
      offRouteBody:
        'We will re-plan from here — the stops and mandatory points are kept.',
      reroute: 're-plan from here',
      onRoute: 'I am on the route',

      // ── Turn-by-turn banner (prominent display in moving mode) ───────────────
      turnInAhead: '{{distance}} — {{instruction}}',
      turnInAheadStandalone: 'in {{distance}}',
      // ── Nearby POI hints (navigator-style inline prompts) ──────────────────
      nearbyService: '{{name}} {{distance}} away',
      nearbyServiceOnRoute: '{{name}} on the way',
      nearbyServiceIfAlong: '{{name}} {{distance}} away, if along the way',
      nearbyToilet: 'toilet',
      nearbyCafe: 'cafe',
      nearbyRestaurant: 'restaurant',
      nearbyHotel: 'hotel',
      nearbyBusStop: 'bus stop',
      nearbyGeneric: 'place',

      // ── Suggestions along the way ────────────────────────────────────────
      suggestionsTitle:
        'on the way — suggestions, they do not change the route',
      suggestionAdd: 'add',
      suggestionSkip: 'no thanks',

      // ── Voice ────────────────────────────────────────────────────────────
      voiceDistance: 'In {{distance}} meters {{instruction}}',
      voiceArrived: 'You have arrived',
      continuingNavigation: 'Continuing navigation…',

      // ── The «моё местоположение» start waypoint ──────────────────────────
      myLocation: 'My location',
      routeStart: 'route start',

      // ── Progress ─────────────────────────────────────────────────────────
      alongLine: 'along the line',
      alongRoute: 'along the route',
      passed: 'walked',
      progressOf: '{{done}} of {{total}}',
      visitsLeft: 'visits left ~{{time}}',
      withTravelLeft: '~{{time}} left including travel',
      lineRemaining: '{{distance}} left',
      progressLabel: 'route progress',
      progressLine: '{{doneWord}} {{percent}}% of the line',

      // ── Next-stop card ───────────────────────────────────────────────────
      distanceToStop: '{{distance}} away',
      visitApprox: 'visit ≈ {{time}}',
      travelFor: '{{verb}} ~{{time}}',
      arrival: 'arrival {{eta}}',
      openInMaps: 'open in maps',

      // ── Stop list ────────────────────────────────────────────────────────
      stopListTitle: 'stops · {{visited}} of {{total}}',

      // ── Route done / nothing to walk ─────────────────────────────────────
      routeDoneTitle: 'route complete',
      routeDoneBody: 'all {{total}} stops are marked — you can start again',
      emptyTitle: 'no route yet',
      emptyBody:
        'Build a route in the planning mode — then come back here to walk it stop by stop.',

      // ── How the guide talks about movement, per transport ────────────────
      modeFootVerb: 'walk',
      modeFootDone: 'walked',
      modeFootLabel: 'on foot',
      modeFootImperative: 'Walk',
      modeBikeVerb: 'ride',
      modeBikeDone: 'ridden',
      modeBikeLabel: 'by bike',
      modeBikeImperative: 'Ride',
      modeCarVerb: 'drive',
      modeCarDone: 'driven',
      modeCarLabel: 'by car',
      modeCarImperative: 'Drive',
      modeCarHint: 'park by the stop',

      // ── Units ────────────────────────────────────────────────────────────
      metres: '{{value}} m',
      km: '{{value}} km',
      minutes_one: '{{count}} min',
      minutes_other: '{{count}} min',
      hours_one: '{{count}} hr',
      hours_other: '{{count}} hrs',
      stops_one: '{{count}} stop',
      stops_other: '{{count}} stops',

      // ── Honest wording for a failed /routes/generate request ─────────────
      agentNotFound:
        'the agent does not answer at this address (404) — the app seems to be pointed at the wrong server. That is a configuration error, not “nothing found”.',
      agentServerError: 'the agent answered with error {{status}} — try again',
      agentDenied:
        'the agent refused the request ({{status}}) — check access to the service.',
      agentBadRequest:
        'the agent did not understand the request ({{status}}) — rephrase what you would like to see.',
    },
  },
} satisfies LocaleArea<{ guide: Record<string, string> }>;
