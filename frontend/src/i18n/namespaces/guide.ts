/** One locale area lives in one file: `ru` and `en` side by side. */
export interface LocaleArea<T = Record<string, unknown>> {
  ru: T;
  en: T;
}

/** Keys of the navigator («Проводник»): stop states, progress, turn phrasing. */
export const guideArea = {
  ru: {
    guide: {
      title: 'Проводник',
      subtitle: 'идём по маршруту остановка за остановкой',
      reset: 'сбросить',
      resetTitle: 'начать маршрут заново',
      soundOn: 'звук вкл',
      soundOff: 'звук выкл',
      enableSound: 'включить звук',
      disableSound: 'выключить звук',

      enter: 'Пойти по маршруту',
      enterHint: 'Проводник отметит пройденное и подскажет повороты',
      exit: 'выйти',

      start: 'начать маршрут',
      overview: 'обзор',
      advance: 'я на месте',
      markNextStop: 'отметить остановку «{{name}}»',
      routeDetails: 'маршрут',
      nextPlaceInfo: 'место, куда вы идёте',
      readPlaceInfo: 'читать о месте',
      hidePlaceInfo: 'свернуть описание',
      hidePanel: 'скрыть панель маршрута',
      nextManeuver: 'следующий манёвр',
      tripProgress: 'прогресс маршрута',
      finish: 'завершить',
      again: 'пройти заново',
      nextStop: 'следующая остановка',
      goToStop: '{{imperative}} к остановке «{{name}}»',
      followRoute: '{{imperative}} по маршруту',

      maneuverIn: 'через {{distance}}',
      noSignal: 'сигнала нет — идите по линии маршрута',
      distanceHidden: 'расстояние скрыто: сигнал GPS неточный',
      announceIn: 'Через {{distance}}: {{instruction}}',

      offRouteTitle: 'вы сошли с маршрута',
      offRouteAt: 'вы в ~{{distance}} от линии.',
      offRouteBody:
        'Перестроим от вас — остановки и обязательные точки сохранятся.',
      reroute: 'перестроить от меня',
      onRoute: 'я на маршруте',

      detailsToggle: 'маршрут · {{visited}} из {{total}}',
      activityTitle: 'детали маршрута',
      activityHide: 'свернуть детали',

      turnInAhead: '{{distance}} — {{instruction}}',
      turnInAheadStandalone: 'через {{distance}}',
      nearbyService: '{{name}} в {{distance}}',
      nearbyServiceOnRoute: '{{name}} по пути',
      nearbyServiceIfAlong: '{{name}} в {{distance}}, если по пути',
      nearbyToilet: 'туалет',
      nearbyCafe: 'кафе',
      nearbyRestaurant: 'ресторан',
      nearbyHotel: 'гостиница',
      nearbyBusStop: 'остановка',
      nearbyGeneric: 'место',

      suggestionsTitle: 'по пути — предложения, маршрут не меняют',
      suggestionAdd: 'добавить',
      suggestionSkip: 'не надо',

      voiceDistance: 'Через {{distance}} метров {{instruction}}',
      voiceArrived: 'Вы прибыли',
      continuingNavigation: 'Продолжаем навигацию…',

      myLocation: 'Моё местоположение',
      routeStart: 'старт маршрута',

      alongLine: 'по линии',
      alongRoute: 'по маршруту',
      passed: 'пройдено',
      progressOf: '{{done}} из {{total}}',
      visitsLeft: 'осталось осмотра ~{{time}}',
      withTravelLeft: 'с дорогой осталось ~{{time}}',
      lineRemaining: 'осталось {{distance}}',
      progressLabel: 'прогресс маршрута',
      progressLine: '{{doneWord}} {{percent}}% линии',

      distanceToStop: 'до неё {{distance}}',
      visitApprox: 'осмотр ≈ {{time}}',
      travelFor: '{{verb}} ~{{time}}',
      arrival: 'прибытие {{eta}}',
      openInMaps: 'открыть в картах',

      stopListTitle: 'остановки · {{visited}} из {{total}}',

      routeDoneTitle: 'маршрут пройден',
      routeDoneBody: 'все {{total}} остановок отмечены — можно начать заново',
      emptyTitle: 'маршрута пока нет',
      emptyBody:
        'Соберите маршрут в режиме планирования — и возвращайтесь сюда, чтобы идти по нему остановка за остановкой.',

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

      agentNotFound:
        'агент не отвечает по этому адресу (404) — похоже, приложение обращается не к тому серверу. Это ошибка настройки, а не «ничего не найдено».',
      agentServerError: 'агент ответил ошибкой {{status}} — попробуйте ещё раз',
      agentNoReader:
        'планировщик не настроен: не задан ключ модели. Сообщите администратору — без ключа маршрут построить нечем',
      agentDenied:
        'агент отклонил запрос ({{status}}) — проверьте доступ к сервису.',
      agentBadRequest:
        'агент не понял запрос ({{status}}) — переформулируйте, что хотите посмотреть.',
    },
  },
  en: {
    guide: {
      title: 'Guide',
      subtitle: 'walking the route stop by stop',
      reset: 'reset',
      resetTitle: 'start the route over',
      soundOn: 'sound on',
      soundOff: 'sound off',
      enableSound: 'turn the sound on',
      disableSound: 'turn the sound off',

      enter: 'Walk the route',
      enterHint: 'The guide marks what you have walked and calls the turns',
      exit: 'exit',

      start: 'start the route',
      overview: 'overview',
      advance: 'I am here',
      markNextStop: 'mark stop “{{name}}”',
      routeDetails: 'route',
      nextPlaceInfo: 'your next place',
      readPlaceInfo: 'read about this place',
      hidePlaceInfo: 'hide place details',
      hidePanel: 'hide route panel',
      nextManeuver: 'next maneuver',
      tripProgress: 'trip progress',
      finish: 'finish',
      again: 'walk it again',
      nextStop: 'next stop',
      goToStop: '{{imperative}} to the stop “{{name}}”',
      followRoute: '{{imperative}} along the route',

      maneuverIn: 'in {{distance}}',
      noSignal: 'no signal — follow the route line',
      distanceHidden: 'distance hidden: the GPS fix is inaccurate',
      announceIn: 'In {{distance}}: {{instruction}}',

      offRouteTitle: 'you have left the route',
      offRouteAt: 'you are ~{{distance}} off the line.',
      offRouteBody:
        'We will re-plan from here — the stops and mandatory points are kept.',
      reroute: 're-plan from here',
      onRoute: 'I am on the route',

      detailsToggle: 'route · {{visited}} of {{total}}',
      activityTitle: 'route details',
      activityHide: 'collapse details',

      turnInAhead: '{{distance}} — {{instruction}}',
      turnInAheadStandalone: 'in {{distance}}',
      nearbyService: '{{name}} {{distance}} away',
      nearbyServiceOnRoute: '{{name}} on the way',
      nearbyServiceIfAlong: '{{name}} {{distance}} away, if along the way',
      nearbyToilet: 'toilet',
      nearbyCafe: 'cafe',
      nearbyRestaurant: 'restaurant',
      nearbyHotel: 'hotel',
      nearbyBusStop: 'bus stop',
      nearbyGeneric: 'place',

      suggestionsTitle:
        'on the way — suggestions, they do not change the route',
      suggestionAdd: 'add',
      suggestionSkip: 'no thanks',

      voiceDistance: 'In {{distance}} meters {{instruction}}',
      voiceArrived: 'You have arrived',
      continuingNavigation: 'Continuing navigation…',

      myLocation: 'My location',
      routeStart: 'route start',

      alongLine: 'along the line',
      alongRoute: 'along the route',
      passed: 'walked',
      progressOf: '{{done}} of {{total}}',
      visitsLeft: 'visits left ~{{time}}',
      withTravelLeft: '~{{time}} left including travel',
      lineRemaining: '{{distance}} left',
      progressLabel: 'route progress',
      progressLine: '{{doneWord}} {{percent}}% of the line',

      distanceToStop: '{{distance}} away',
      visitApprox: 'visit ≈ {{time}}',
      travelFor: '{{verb}} ~{{time}}',
      arrival: 'arrival {{eta}}',
      openInMaps: 'open in maps',

      stopListTitle: 'stops · {{visited}} of {{total}}',

      routeDoneTitle: 'route complete',
      routeDoneBody: 'all {{total}} stops are marked — you can start again',
      emptyTitle: 'no route yet',
      emptyBody:
        'Build a route in the planning mode — then come back here to walk it stop by stop.',

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

      metres: '{{value}} m',
      km: '{{value}} km',
      minutes_one: '{{count}} min',
      minutes_other: '{{count}} min',
      hours_one: '{{count}} hr',
      hours_other: '{{count}} hrs',
      stops_one: '{{count}} stop',
      stops_other: '{{count}} stops',

      agentNotFound:
        'the agent does not answer at this address (404) — the app seems to be pointed at the wrong server. That is a configuration error, not “nothing found”.',
      agentServerError: 'the agent answered with error {{status}} — try again',
      agentNoReader:
        'the planner is not configured: no model key is set. Tell the administrator — without one there is nothing to plan with',
      agentDenied:
        'the agent refused the request ({{status}}) — check access to the service.',
      agentBadRequest:
        'the agent did not understand the request ({{status}}) — rephrase what you would like to see.',
    },
  },
} satisfies LocaleArea<{ guide: Record<string, string> }>;
