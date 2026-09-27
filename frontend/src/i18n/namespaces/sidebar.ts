import type { LocaleArea } from './guide';


/**
 * Keys of the panel's own controls: filters, budgets, transport, statuses.
 *
 * Careful with the filter options: their `code` is the backend's canonical
 * category («замок»), which goes to the agent untouched; only the visible label
 * is translated. Translating the code would silently stop filtering.
 */
/** The shape both locales must satisfy — kept explicit so a missing key fails. */
type SidebarShape = {
  budgets: Record<'b30' | 'b60' | 'b120' | 'b240' | 'none', string>;
  transport: Record<'pedestrian' | 'bicycle' | 'car' | 'any', string>;
  resultModes: Record<'route' | 'catalogue', string>;
  interests: Record<
    | 'castles'
    | 'catholic'
    | 'orthodox'
    | 'monasteries'
    | 'museums'
    | 'estates'
    | 'parks'
    | 'monuments',
    string
  >;
  amenities: Record<'toilet' | 'cafe', string>;
  avoid: Record<'cemeteries' | 'infrastructure' | 'hotels', string>;
  status: Record<
    | 'offline'
    | 'noToilets'
    | 'cancelled'
    | 'fewPlaces'
    | 'nothingFound'
    | 'noChanges'
    | 'restoredPrevious',
    string
  >;
  geo: Record<
    | 'label'
    | 'unsupported'
    | 'failedShort'
    | 'denied'
    | 'searching'
    | 'detect'
    | 'allow'
    | 'startUnset'
    | 'startIsMe',
    string
  >;
  visit: Record<
    | 'label'
    | 'estimate'
    | 'mine'
    | 'unit'
    | 'open'
    | 'openEstimate'
    | 'minus'
    | 'plus'
    | 'reset'
    | 'resetHint'
    | 'chipEstimate'
    | 'chipMine'
    | 'estimatedHint',
    string
  >;
  ui: Record<
    | 'timeLabel'
    | 'transportLabel'
    | 'emptyStops'
    | 'emptyPoint'
    | 'guide'
    | 'refresh'
    | 'moreFilters'
    | 'considering'
    | 'route'
    | 'noLimit'
    | 'addPoint'
    | 'catalogue'
    | 'roundTrip'
    | 'point'
    | 'plan'
    | 'build'
    | 'start'
    | 'myLocation',
    string
  >;
};


export const sidebarArea = {
  ru: {
    sidebar: {
    budgets: {
      b30: '30 мин',
      b60: '1 ч',
      b120: '2 ч',
      b240: 'полдня',
      none: 'без ограничения',
    },
    transport: {
      pedestrian: 'пешком',
      bicycle: 'велосипед',
      car: 'машина',
      any: 'как удобно',
    },
    resultModes: {
      route: 'маршрут',
      catalogue: 'каталог',
    },
    interests: {
      castles: 'замки',
      catholic: 'костёлы',
      orthodox: 'церкви',
      monasteries: 'монастыри',
      museums: 'музеи',
      estates: 'усадьбы',
      parks: 'парки',
      monuments: 'памятники',
    },
    amenities: {
      toilet: 'туалет',
      cafe: 'кафе / перерыв',
    },
    avoid: {
      cemeteries: 'кладбища',
      infrastructure: 'инфраструктура',
      hotels: 'гостиницы',
    },
    status: {
      offline: 'нет связи с агентом — проверьте сеть и попробуйте ещё раз',
      noToilets: 'В базе не нашлось туалетов — маршрут построен без них.',
      cancelled: 'Запрос отменён — маршрут остался прежним.',
      fewPlaces: 'нашёл меньше 2 мест — попробуйте уточнить запрос',
      nothingFound: 'ничего не нашлось',
      noChanges: 'без изменений',
      restoredPrevious: 'вернул предыдущий маршрут',
    },
    geo: {
      unsupported: 'браузер не умеет геолокацию',
      failedShort: 'не удалось определить',
      denied: 'браузер запретил доступ',
      label: 'геолокация',
      searching: 'ищу вас…',
      detect: 'определить моё местоположение',
      allow: 'разрешить',
      startUnset: 'старт не задан',
      startIsMe: 'старт — моё местоположение',
    },
    visit: {
      label: 'время осмотра',
      estimate: 'оценка из данных',
      mine: 'ваше время',
      unit: 'мин',
      open: 'время осмотра: {{minutes}} минут, изменить',
      openEstimate: 'время осмотра: примерно {{minutes}} минут, изменить',
      minus: 'убавить время осмотра на {{minutes}} минут',
      plus: 'прибавить время осмотра на {{minutes}} минут',
      reset: 'вернуть оценку',
      resetHint: 'вернуть примерно {{minutes}} мин',
      chipEstimate: '≈ {{minutes}} мин',
      chipMine: '{{minutes}} мин',
      estimatedHint: 'обычно здесь оставляют ≈ {{minutes}} мин',
    },
    units: {
      km: '{{value}} км',
      minutes_one: '{{count}} мин',
      minutes_few: '{{count}} мин',
      minutes_many: '{{count}} мин',
      hours_one: '{{count}} ч',
      hours_few: '{{count}} ч',
      hours_many: '{{count}} ч',
    },
    ui: {
      timeLabel: 'сколько есть времени',
      transportLabel: 'на чём',
      emptyStops:
        'Здесь появятся остановки маршрута — или соберите его из точек вручную',
      emptyPoint: 'пустая точка (выбрать кликом по карте)',
      guide: 'Проводник',
      refresh: 'обновить',
      moreFilters: 'ещё фильтры',
      considering: 'учитываю:',
      route: 'Маршрут',
      noLimit: 'без лимита',
      addPoint: 'Добавить точку',
      catalogue: 'каталог мест',
      roundTrip: 'круговой маршрут',
      point: 'точка',
      plan: 'Строю маршрут…',
      build: 'Подобрать маршрут',
      start: 'старт маршрута',
      myLocation: 'Моё местоположение',
    },
    },
  },
  en: {
    sidebar: {
    budgets: {
      b30: '30 min',
      b60: '1 hr',
      b120: '2 hrs',
      b240: 'half a day',
      none: 'no limit',
    },
    transport: {
      pedestrian: 'on foot',
      bicycle: 'by bike',
      car: 'by car',
      any: 'any way',
    },
    resultModes: {
      route: 'route',
      catalogue: 'catalogue',
    },
    interests: {
      castles: 'castles',
      catholic: 'Catholic churches',
      orthodox: 'Orthodox churches',
      monasteries: 'monasteries',
      museums: 'museums',
      estates: 'manor houses',
      parks: 'parks',
      monuments: 'monuments',
    },
    amenities: {
      toilet: 'toilet',
      cafe: 'cafe / a break',
    },
    avoid: {
      cemeteries: 'cemeteries',
      infrastructure: 'infrastructure',
      hotels: 'hotels',
    },
    status: {
      offline: 'the agent is unreachable — check the network and try again',
      noToilets: 'No toilets found in the data — the route was built without them.',
      cancelled: 'Request cancelled — the route is unchanged.',
      fewPlaces: 'fewer than 2 places found — try narrowing the request',
      nothingFound: 'nothing found',
      noChanges: 'no changes',
      restoredPrevious: 'restored the previous route',
    },
    geo: {
      unsupported: 'this browser has no geolocation',
      failedShort: 'could not determine it',
      denied: 'the browser blocked access',
      label: 'geolocation',
      searching: 'finding you…',
      detect: 'use my location',
      allow: 'allow',
      startUnset: 'no start set',
      startIsMe: 'start — my location',
    },
    visit: {
      label: 'visit time',
      estimate: 'an estimate from the dataset',
      mine: 'your own time',
      unit: 'min',
      open: 'visit time: {{minutes}} minutes, change',
      openEstimate: 'visit time: about {{minutes}} minutes, change',
      minus: 'less visit time, by {{minutes}} minutes',
      plus: 'more visit time, by {{minutes}} minutes',
      reset: 'use the estimate',
      resetHint: 'back to about {{minutes}} min',
      chipEstimate: '≈ {{minutes}} min',
      chipMine: '{{minutes}} min',
      estimatedHint: 'people usually spend about {{minutes}} min here',
    },
    units: {
      km: '{{value}} km',
      minutes_one: '{{count}} min',
      minutes_other: '{{count}} min',
      hours_one: '{{count}} hr',
      hours_other: '{{count}} hrs',
    },
    ui: {
      timeLabel: 'how much time you have',
      transportLabel: 'how you travel',
      emptyStops: 'Route stops will appear here — or build one by hand',
      emptyPoint: 'an empty stop (pick it on the map)',
      guide: 'Guide',
      refresh: 'refresh',
      moreFilters: 'more filters',
      considering: 'taking into account:',
      route: 'Route',
      noLimit: 'no limit',
      addPoint: 'Add a stop',
      catalogue: 'place catalogue',
      roundTrip: 'round trip',
      point: 'stop',
      plan: 'Building the route…',
      build: 'Plan my route',
      start: 'route start',
      myLocation: 'My location',
    },
    },
  },
} satisfies LocaleArea<{ sidebar: SidebarShape & { units: Record<string, string> } }>;

