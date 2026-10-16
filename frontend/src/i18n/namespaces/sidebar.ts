import type { LocaleArea } from './guide';

/** Keys of the panel's own controls: filters, budgets, transport, statuses. */
/** The shape both locales must satisfy — kept explicit so a missing key fails. */
type SidebarShape = {
  budgets: Record<
    'b30' | 'b45' | 'b60' | 'b120' | 'b180' | 'b240' | 'b480' | 'none',
    string
  >;
  transport: Record<'pedestrian' | 'bicycle' | 'car' | 'any', string>;
  when: Record<'now' | 'today' | 'tomorrow' | 'custom', string>;
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
  amenities: Record<
    'toilet' | 'cafe' | 'restaurant' | 'hotel' | 'transitStop',
    string
  >;
  avoid: Record<
    'museums' | 'cemeteries' | 'infrastructure' | 'hotels' | 'religious',
    string
  >;
  status: Record<
    | 'offline'
    | 'noToilets'
    | 'cancelled'
    | 'fewPlaces'
    | 'nothingFound'
    | 'noChanges'
    | 'restoredPrevious'
    | 'simulatedFix'
    | 'outsideCoverageTitle'
    | 'outsideCoverageHint'
    | 'impossibleTitle'
    | 'degradedTitle'
    | 'reasonFallback'
    | 'reason'
    | string,
    string | Record<string, string>
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
    | 'repairing_route'
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
    | 'whenLabel'
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
    | 'refreshRoute'
    | 'refreshingRoute'
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
      when: {
        now: 'сейчас',
        today: 'сегодня 18:00',
        tomorrow: 'завтра 10:00',
        custom: 'своё время',
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
        transitStop: 'остановка транспорта',
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
        simulatedFix:
          'СИМУЛЯЦИЯ GPS: положение проигрывается, телефон ни при чём',
        restoredPrevious: 'вернул предыдущий маршрут',
        outsideCoverageTitle:
          'Сюда маршрут не построить: {{names}} — вне зоны покрытия (Гродненская область)',
        outsideCoverageHint: 'Попробуйте точку внутри области',
        impossibleTitle:
          'Маршрут не построен: не удалось выполнить обязательное требование',
        degradedTitle: 'Часть запроса выполнить не удалось',
        checkedAt: 'Проверено на {{when}}',
        reasonFallback: 'требование не выполнено',
        reason: {
          must_visit_on_route: 'обязательная точка не попала на маршрут',
          must_visit_absent: 'обязательная точка не найдена в данных',
          must_visit_unroutable: 'до обязательной точки нет дороги',
          must_visit_outside_coverage: 'обязательная точка вне зоны покрытия',
          service_on_route: 'удобство не попало на маршрут',
          service_along_route: 'удобство не рядом с маршрутом',
          hard_service_absent: 'обязательное удобство не найдено в данных',
          soft_service_absent: 'удобство не найдено в данных',
          service_not_measured: 'удобство не измерено',
          interest_absent: 'интерес не найден в данных',
          avoid_honoured: 'ограничение соблюдено',
          avoid_violated: 'ограничение нарушено',
          free_entry_proven: 'вход бесплатный у всех остановок',
          paid_stop_on_route: 'на маршруте есть платный вход',
          admission_unknown: 'цена входа не указана в данных',
          open_at_visit: 'в это время всё открыто',
          closed_at_visit: 'в это время остановка закрыта',
          hours_unknown: 'часы работы не указаны в данных',
          visit_time_unknown: 'время прогулки не названо',
          step_free_proven: 'на пути нет лестниц',
          steps_on_route: 'на маршруте есть лестницы',
          steps_unknown: 'лестницы не удалось проверить',
          route_missing: 'маршрут не построен',
          geometry_missing: 'нет геометрии маршрута',
        },
        kind: {
          free: 'бесплатный вход',
          open: 'открыто в это время',
          step_free: 'без лестниц',
        },
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
        repairing_route: 'пересобираю маршрут, чтобы всё сошлось',
        drawing_line: 'рисую линию по дорогам',
        checking_requirements: 'проверяю, что всё выполнено',
        done: 'готово',
      },

      ui: {
        timeLabel: 'сколько есть времени',
        whenLabel: 'когда',
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
        refreshRoute: 'Обновить маршрут',
        refreshingRoute: 'Обновляю маршрут…',
        start: 'старт маршрута',
        myLocation: 'Моё местоположение',
      },

      plan: {
        geoOverride: 'переопределить, откуда начинается маршрут',
        filtersPrecedence: 'применю фильтры поверх текста запроса — они важнее',
        /** Example query: a real Grodno place name in the interface's alphabet. */
        queryPlaceholder: 'Каложская церковь, Гродно',
        partyLabel: 'кто идёт',
        adults: 'взрослые',
        children: 'дети',
        childrenAges: 'возраст детей, если знаете',
        amenities: 'удобства в пути',
        hard: 'обязательно',
        soft: 'желательно',
        interests: 'интересы',
        avoid: 'избегать',
        resultType: 'что показать',
        resultTypeAria: 'тип результата',
        reset: 'сброс',
        resetHint: 'очистить маршрут',
        recalculating: 'пересчитываю маршрут…',
        optimiseOrder: 'оптимизировать порядок',
        optimiseHint: 'порядок точек задан вручную — вернуть его планировщику',
        length: 'длина',
        travelMinutes: 'мин в пути',
        travelSummary: 'в пути ~{{value}}',
        visitSummary: 'осмотр ~{{value}}',
        budgetSummary: 'лимит {{value}}',
        overBudget: 'не влезло в лимит',
        refinementAdded: 'добавил: {{names}}',
        refinementRemoved: 'убрал: {{names}}',
        addedCount: 'добавлено {{count}}',
        removedCount: 'убрано {{count}}',
        excludedManual: 'убрано вручную: {{count}}',
        undoRefinement: 'отменить уточнение',
        newRoute: 'новый маршрут',
        manualAria: 'добавить точку в маршрут',
        manualSearchAria: 'найти и добавить точку',
        hintsAria: 'подсказки',
        tabsAria: 'раздел панели',
        decrement: 'убавить: {{label}}',
        increment: 'прибавить: {{label}}',
      },
      summary: {
        adults: '{{count}} взр.',
        ages: 'возраст {{ages}}',
        hard: 'обязательно: {{label}}',
        soft: 'желательно: {{label}}',
        interest: 'интерес: {{label}}',
        avoid: 'без {{label}}',
        children_one: '{{count}} ребёнок',
        children_few: '{{count}} ребёнка',
        children_many: '{{count}} детей',
      },
      waypoints: {
        move: 'переместить: {{name}}',
        dragHint: 'перетащить · стрелки вверх/вниз',
        start: 'старт',
        stop: 'остановка {{number}}',
        /** A stop added by hand without a name still needs something to show. */
        unnamed: 'точка',
        unnamedWithId: 'точка {{id}}',
        pin: 'закрепить',
        unpin: 'открепить',
        pinHint: 'закрепить: уточнение не уберёт эту точку',
        pinnedHint: 'уточнение не будет убирать эту точку',
        remove: 'удалить',
      },
      catalogue: {
        title: 'Каталог мест',
        intro:
          'Это выбор мест, а не готовая прогулка — нажмите на место, чтобы показать его на карте.',
        count_one: '{{count}} место',
        count_few: '{{count}} места',
        count_many: '{{count}} мест',
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
      when: {
        now: 'now',
        today: 'today 18:00',
        tomorrow: 'tomorrow 10:00',
        custom: 'custom time',
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
        transitStop: 'transit stop',
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
        noToilets:
          'No toilets found in the data — the route was built without them.',
        cancelled: 'Request cancelled — the route is unchanged.',
        fewPlaces: 'fewer than 2 places found — try narrowing the request',
        nothingFound: 'nothing found',
        noChanges: 'no changes',
        simulatedFix:
          'GPS SIMULATION: the position is replayed, no phone involved',
        restoredPrevious: 'restored the previous route',
        outsideCoverageTitle:
          'No route here: {{names}} — outside the coverage area (Hrodna region)',
        outsideCoverageHint: 'Try a point inside the region',
        impossibleTitle:
          'Route not built: a mandatory requirement could not be met',
        degradedTitle: 'Part of the request could not be fulfilled',
        checkedAt: 'Checked for {{when}}',
        reasonFallback: 'requirement not met',
        reason: {
          must_visit_on_route: 'a mandatory stop is not on the route',
          must_visit_absent: 'a mandatory stop is not in the data',
          must_visit_unroutable: 'no road leads to a mandatory stop',
          must_visit_outside_coverage:
            'a mandatory stop is outside the coverage area',
          service_on_route: 'the amenity is not on the route',
          service_along_route: 'the amenity is not along the route',
          hard_service_absent: 'a required amenity is not in the data',
          soft_service_absent: 'the amenity is not in the data',
          service_not_measured: 'the amenity was not measured',
          interest_absent: 'the interest is not in the data',
          avoid_honoured: 'the restriction was honoured',
          avoid_violated: 'the restriction was violated',
          free_entry_proven: 'every stop is free to enter',
          paid_stop_on_route: 'a stop on the route charges admission',
          admission_unknown: 'the data states no admission price',
          open_at_visit: 'everything is open at that hour',
          closed_at_visit: 'a stop is closed at that hour',
          hours_unknown: 'the data states no opening hours',
          visit_time_unknown: 'the walk has no stated time',
          step_free_proven: 'no stairs on the route',
          steps_on_route: 'the route crosses stairs',
          steps_unknown: 'the stairs could not be checked',
          route_missing: 'no route was built',
          geometry_missing: 'no route geometry',
        },
        kind: {
          free: 'free admission',
          open: 'open at the time',
          step_free: 'step-free',
        },
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
        repairing_route: 'rebuilding the route so it all holds',
        drawing_line: 'drawing the line along the roads',
        checking_requirements: 'checking that everything is honoured',
        done: 'done',
      },

      ui: {
        timeLabel: 'how much time you have',
        whenLabel: 'when',
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
        refreshRoute: 'Refresh the route',
        refreshingRoute: 'Refreshing the route…',
        start: 'route start',
        myLocation: 'My location',
      },

      plan: {
        geoOverride: 'change where the route starts',
        filtersPrecedence:
          'your filters are applied on top of the text — they take priority',
        queryPlaceholder: 'Kalozha church, Grodno',
        partyLabel: 'who is going',
        adults: 'adults',
        children: 'children',
        childrenAges: 'children’s ages, if you know them',
        amenities: 'amenities en route',
        hard: 'required',
        soft: 'preferred',
        interests: 'interests',
        avoid: 'avoid',
        resultType: 'what to show',
        resultTypeAria: 'result type',
        reset: 'reset',
        resetHint: 'clear the route',
        recalculating: 're-measuring the route…',
        optimiseOrder: 'optimise the order',
        optimiseHint:
          'the stops are in the order you put them in — hand them back to the planner',
        length: 'length',
        travelMinutes: 'min travelling',
        travelSummary: 'travelling ~{{value}}',
        visitSummary: 'visiting ~{{value}}',
        budgetSummary: 'limit {{value}}',
        overBudget: 'over the limit',
        refinementAdded: 'added: {{names}}',
        refinementRemoved: 'removed: {{names}}',
        addedCount: 'added {{count}}',
        removedCount: 'removed {{count}}',
        excludedManual: 'removed by hand: {{count}}',
        undoRefinement: 'undo the refinement',
        newRoute: 'new route',
        manualAria: 'add a stop to the route',
        manualSearchAria: 'find and add a stop',
        hintsAria: 'hints',
        tabsAria: 'panel section',
        decrement: 'decrease: {{label}}',
        increment: 'increase: {{label}}',
      },
      summary: {
        adults: '{{count}} adults',
        ages: 'ages {{ages}}',
        hard: 'required: {{label}}',
        soft: 'preferred: {{label}}',
        interest: 'interest: {{label}}',
        avoid: 'without {{label}}',
        children_one: '{{count}} child',
        children_other: '{{count}} children',
      },
      waypoints: {
        move: 'move: {{name}}',
        unnamed: 'point',
        unnamedWithId: 'point {{id}}',
        dragHint: 'drag · arrow keys up/down',
        start: 'start',
        stop: 'stop {{number}}',
        pin: 'pin',
        unpin: 'unpin',
        pinHint: 'pin: a refinement will not remove this stop',
        pinnedHint: 'a refinement will not remove this stop',
        remove: 'delete',
      },
      catalogue: {
        title: 'Place catalogue',
        intro:
          'These are places to choose from, not a ready walk — tap a place to show it on the map.',
        count_one: '{{count}} place',
        count_other: '{{count}} places',
      },
    },
  },
} satisfies LocaleArea<{
  sidebar: SidebarShape & {
    units: Record<string, string>;
    plan: Record<string, string>;
    summary: Record<string, string>;
    waypoints: Record<string, string>;
    catalogue: Record<string, string>;
  };
}>;
