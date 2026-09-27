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
  budgets: Record<
    'b30' | 'b45' | 'b60' | 'b120' | 'b180' | 'b240' | 'b480' | 'none',
    string
  >;
  transport: Record<'pedestrian' | 'bicycle' | 'car' | 'any', string>;
  resultModes: Record<'route' | 'catalogue', string>;
  interests: Record<
    | 'castles'
    | 'palaces'
    | 'estates'
    | 'catholic'
    | 'orthodox'
    | 'monasteries'
    | 'museums'
    | 'monuments'
    | 'architecture'
    | 'parks'
    | 'religious',
    string
  >;
  amenities: Record<'toilet' | 'cafe' | 'restaurant' | 'hotel', string>;
  avoid: Record<'museums' | 'cemeteries' | 'infrastructure' | 'hotels' | 'religious', string>;
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
  /** The pipeline's own stages, keyed by the codes it sends (`agent/progress.py`). */
  progress: Record<
    | 'cancel'
    | 'waitingRequest'
    | 'waitingLine'
    | 'longWait'
    | 'elapsed_one'
    | 'elapsed_few'
    | 'elapsed_many'
    | 'elapsed_other'
    | 'interpreting_request'
    | 'searching_places'
    | 'selecting_candidates'
    | 'measuring_legs'
    | 'ordering_stops'
    | 'drawing_line'
    | 'checking_requirements'
    | 'done',
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
      b45: '45 мин',
      b60: '1 ч',
      b120: '2 ч',
      b180: '3 ч',
      b240: 'полдня',
      b480: 'весь день',
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
      palaces: 'дворцы',
      catholic: 'костёлы',
      orthodox: 'церкви',
      monasteries: 'монастыри',
      museums: 'музеи',
      estates: 'усадьбы',
      parks: 'парки',
      religious: 'всё религиозное',
      monuments: 'памятники',
      architecture: 'архитектура',
    },
    amenities: {
      toilet: 'туалет',
      cafe: 'кафе / перерыв',
      restaurant: 'ресторан',
      hotel: 'гостиница',
    },
    avoid: {
      museums: 'музеи',
      cemeteries: 'кладбища',
      infrastructure: 'инфраструктура',
      hotels: 'гостиницы',
      religious: 'религиозные места',
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
    // What the pipeline itself reports while a route is being built. Codes come
    // from the backend (`agent/progress.py`); the wording is ours, in both
    // languages, because the server does not speak one.
    progress: {
      cancel: 'отменить',
      elapsed_one: '{{count}} с',
      elapsed_few: '{{count}} с',
      elapsed_many: '{{count}} с',
      elapsed_other: '{{count}} с',
      waitingRequest: 'отправил запрос — жду план от агента',
      waitingLine: 'план пришёл — рисую маршрут по дорогам',
      longWait: 'агент всё ещё строит — иногда это занимает до минуты',
      interpreting_request: 'определяю, что вы просите',
      searching_places: 'ищу точки рядом',
      selecting_candidates: 'отбираю, что взять',
      measuring_legs: 'считаю дорогу между точками',
      ordering_stops: 'собираю порядок остановок',
      drawing_line: 'рисую линию по дорогам',
      checking_requirements: 'проверяю, что всё выполнено',
      done: 'готово',
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
      b45: '45 min',
      b60: '1 hr',
      b120: '2 hrs',
      b180: '3 hrs',
      b240: 'half a day',
      b480: 'a whole day',
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
      palaces: 'palaces',
      catholic: 'Catholic churches',
      orthodox: 'Orthodox churches',
      monasteries: 'monasteries',
      museums: 'museums',
      estates: 'manor houses',
      parks: 'parks',
      religious: 'all religious sites',
      monuments: 'monuments',
      architecture: 'architecture',
    },
    amenities: {
      toilet: 'toilet',
      cafe: 'cafe / a break',
      restaurant: 'restaurant',
      hotel: 'hotel',
    },
    avoid: {
      museums: 'museums',
      cemeteries: 'cemeteries',
      infrastructure: 'infrastructure',
      hotels: 'hotels',
      religious: 'religious sites',
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
    progress: {
      cancel: 'cancel',
      // English plural rules only ever pick _one/_other, but the dictionary
      // shape is shared with Russian, so the categories both languages need are
      // declared here rather than left for one of them to be missing a key.
      elapsed_one: '{{count}} s',
      elapsed_other: '{{count}} s',
      elapsed_few: '{{count}} s',
      elapsed_many: '{{count}} s',
      waitingRequest: 'request sent — waiting for the plan',
      waitingLine: 'the plan is here — drawing the route along the roads',
      longWait: 'the agent is still working — this sometimes takes a minute',
      interpreting_request: 'working out what you asked for',
      searching_places: 'looking for places nearby',
      selecting_candidates: 'choosing what to include',
      measuring_legs: 'measuring the way between stops',
      ordering_stops: 'putting the stops in order',
      drawing_line: 'drawing the line along the roads',
      checking_requirements: 'checking that everything is honoured',
      done: 'done',
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

