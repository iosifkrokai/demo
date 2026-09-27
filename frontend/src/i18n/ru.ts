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
  app: {
    title: 'AI-гид по Гродно',
    description: 'Планировщик маршрутов по Гродно и области',
  },
  tabs: {
    plan: 'Планирование',
    planShort: 'План',
    itineraries: 'Готовые маршруты',
    itinerariesShort: 'Готовые',
    history: 'История',
  },
  tabSubtitles: {
    plan: 'Опишите запрос — соберу маршрут по дорогам',
    itineraries: 'Готовые маршруты — начать с одного из них',
    history: 'Маршруты, которые вы уже построили',
  },
  ask: {
    placeholderRefine: 'Что уточнить? «добавь кофейню»',
    placeholder: 'Что хотите посмотреть?',
    label: 'что хотите посмотреть',
    chips: {
      castles: 'замки',
      churches: 'костёлы',
      monasteries: 'монастыри',
      food: 'где поесть',
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
    build: 'Построить маршрут',
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
    empty: 'Готовые маршруты не загрузились',
    incomplete: 'Одна из точек маршрута не нашлась в данных',
    open: 'показать на карте',
    stops: 'остановки',
    visit: 'осмотр',
    onFoot: 'пешком',
    byCar: 'на машине',
  },
  language: {
    label: 'Язык интерфейса',
    ru: 'Русский',
    en: 'English',
  },
};

/** The shape every other locale must satisfy (see `en.ts`). */
export type Dictionary = typeof ru;
