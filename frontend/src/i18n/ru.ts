/**
 * Russian strings — the source of truth for the UI.
 *
 * Every key here exists in `en`, and a test asserts the two dictionaries have
 * the same shape: a missing translation must fail the build, not appear as a
 * raw key in front of a tourist. Strings that the backend already returns with
 * its own text (place names, blurbs, turn instructions) are not here: they come
 * from data and are switched by the request's `language`, not by the UI.
 */
export const ru = {
  panel: {
    resize: 'изменять ширину панели',
  },
  app: {
    title: 'AI-гид по Гродно',
    description: 'Планировщик маршрутов по Гродно и области',
  },
  tabs: {
    plan: 'Планирование',
    planShort: 'План',
    itineraries: 'Готовые маршруты',
    itinerariesShort: 'Готовые',
    places: 'Все точки',
    placesShort: 'Точки',
    history: 'История',
  },
  tabSubtitles: {
    plan: 'Опишите запрос — соберу маршрут по дорогам',
    itineraries: 'Готовые маршруты — начать с одного из них',
    places: 'Весь каталог — на карте и списком',
    history: 'Маршруты, которые вы уже построили',
  },
  ask: {
    placeholderRefine: 'Что уточнить? «добавь кофейню»',
    placeholder: 'Что хотите посмотреть?',
    label: 'что хотите посмотреть',
    chips: {
      // A chip *is* the query: it goes to the agent exactly as written, so it
      // reads like something a tourist would say, not like a filter name.
      oldTown: 'Старый город за два часа пешком',
      castlesChurches: 'Замки и костёлы Гродно',
      food: 'Где поесть в центре, недорого',
      evening: 'Вечерняя прогулка по Советской',
      withChildren: 'С детьми: парки и замки',
    },
    // Offered only when a route already exists: these edit it instead of
    // planning a new one. Each is an instruction the pipeline can honour —
    // add a stop, exclude one, shorten the budget, narrow the interests.
    chipsRefine: {
      addCafe: 'добавь кафе по пути',
      removeMuseum: 'убери музей из маршрута',
      shorter: 'сделай короче — часа на два',
      onlyChurches: 'оставь только костёлы и замки',
      withChildren: 'добавь что-нибудь для детей',
    },
  },
  transport: {
    pedestrian: 'пешком',
    bicycle: 'велосипед',
    auto: 'машина',
    any: 'как удобно',
  },
  budget: {
    '30': '30 мин',
    '60': '1 ч',
    '120': '2 ч',
    '240': 'полдня',
    any: 'без ограничения',
  },
  actions: {
    closePanel: 'закрыть панель',
    build: 'Подобрать маршрут',
    cancel: 'Отменить',
  },
  guide: {
    enter: 'Пойти по маршруту',
    enterHint: 'Проводник отметит пройденное и подскажет повороты',
    title: 'Проводник',
    exit: 'выйти',
    stops_one: '{{count}} остановка',
    stops_few: '{{count}} остановки',
    stops_many: '{{count}} остановок',
  },
  history: {
    empty: 'Пока пусто — построенные маршруты появятся здесь',
    walked: 'пройден',
    walkedPartial: 'пройдено {{visited}} из {{total}}',
    restore: 'вернуть на карту',
    clear: 'Очистить историю',
    remove: 'Убрать из истории',
  },
  itineraries: {
    loading: 'Загружаю готовые маршруты…',
    loadFailed:
      'Не удалось загрузить готовые маршруты. Сами маршруты целы — не ответил агент.',
    retry: 'повторить',
    emptyList: 'Готовых маршрутов пока нет.',
    intro:
      'Собраны вручную из реальных точек данных: открываются сразу, без запроса к модели — потом маршрут можно править как обычно.',
    incompleteCount:
      'Часть точек не нашлась в данных ({{count}}) — такие маршруты показаны не полностью.',
    empty: 'Готовые маршруты не загрузились',
    incomplete: 'Одна из точек маршрута не нашлась в данных',
    open: 'показать на карте',
    stops: 'остановки',
    visit: 'осмотр',
    alongTheWay: 'по пути',
    onFoot: 'пешком',
    byCar: 'на машине',
  },
  places: {
    loading: 'Загружаю все точки…',
    loadFailed: 'Не удалось загрузить точки. Данные целы — не ответил агент.',
    retry: 'повторить',
    search: 'Название, город или категория',
    intro: '{{count}} в данных — весь каталог на карте и в списке.',
    noMatch: 'Ничего не нашлось — попробуйте другое слово.',
  },
  language: {
    label: 'Язык интерфейса',
    ru: 'Русский',
    en: 'English',
  },
};

/** The shape every other locale must satisfy (see `en.ts`). */
export type Dictionary = typeof ru;
