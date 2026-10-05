import type { LocaleArea } from './guide';

/** Keys of the map itself: my position, the planner entry, waypoint rows. */
export const mapArea = {
  ru: {
    map: {
      myLocation: 'Моё местоположение',
      routeStart: 'старт маршрута',
      locateFailed: 'не удалось определить ваше местоположение',
      locateDenied: 'браузер запретил доступ к геолокации',
      noGeolocation: 'браузер не умеет геолокацию',
      searching: 'ищу вас…',
      planRoute: 'Планировать маршрут',
      panelToggle: 'открыть или закрыть панель маршрута',
      panelOpen: 'панель маршрута открыта',
      // The place card, on both surfaces: the popup by the pin on a wide screen
      // and the card at the bottom of the map on a phone. The headings used to
      // be Russian-only strings baked into the popup, which meant an
      // English-language tourist read «Ещё факты» on an otherwise English card.
      placeClose: 'Закрыть',
      placeMore: 'Ещё о месте',
      placeLess: 'Свернуть',
      placeMoreFacts: 'Ещё факты',
      placeReadMore: 'Почитать',
      dragHint: 'перетащить · стрелки вверх/вниз',
      lineFromApp: 'Линия маршрута — построена в приложении',
      lineFromAgent: 'Линия маршрута — из проверенного плана агента',
      lineMissing:
        'Агент вернул остановки без проверенной линии — линия не показана',
      followMe: 'следить за мной',
      following: 'слежу за вами',
      northUp: 'север сверху',
      headingUp: 'по курсу',
      servicesToggle: 'что рядом по пути',
      // What the guide knows about the places beside the route, said plainly
      // when one is tapped: how far off the line it is, whether its hours are
      // known at all, and that the walk to reach it has not been worked out.
      // The count that used to live here moved out with the summary card; the
      // guide is never asked to *count* things, only to place them.
      serviceOffLine: '{{metres}} м в сторону от маршрута',
      serviceHoursUnknown: 'часы неизвестны',
      serviceNoDetour: 'время на заход не рассчитано',
    },
  },
  en: {
    map: {
      myLocation: 'My location',
      routeStart: 'route start',
      locateFailed: 'could not determine your location',
      locateDenied: 'the browser blocked access to geolocation',
      noGeolocation: 'this browser has no geolocation',
      searching: 'finding you…',
      planRoute: 'Plan a route',
      panelToggle: 'open or close the route panel',
      panelOpen: 'the route panel is open',
      placeClose: 'Close',
      placeMore: 'More about this place',
      placeLess: 'Show less',
      placeMoreFacts: 'More facts',
      placeReadMore: 'Read more',
      dragHint: 'drag · arrow keys',
      lineFromApp: 'Line drawn in this app',
      lineFromAgent: "Line comes from the agent's verified plan",
      lineMissing:
        'The agent returned stops without a verified line — no line shown',
      followMe: 'follow me',
      following: 'following you',
      northUp: 'north up',
      headingUp: 'heading up',
      servicesToggle: "what's along the way",
      // What the guide knows about the places beside the route, said plainly
      // when one is tapped: how far off the line it is, whether its hours are
      // known at all, and that the walk to reach it has not been worked out.
      // The count that used to live here moved out with the summary card; the
      // guide is never asked to *count* things, only to place them.
      serviceOffLine: '{{metres}} m off the route',
      serviceHoursUnknown: 'hours unknown',
      serviceNoDetour: 'time to reach it is not computed',
    },
  },
} satisfies LocaleArea<{ map: Record<string, string> }>;
