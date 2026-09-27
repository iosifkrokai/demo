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
      panelOpen: 'панель маршрута открыта',
      dragHint: 'перетащить · стрелки вверх/вниз',
      lineFromApp: 'Линия маршрута — построена в приложении',
      lineFromAgent: 'Линия маршрута — из проверенного плана агента',
      lineMissing: 'Агент вернул остановки без проверенной линии — линия не показана',
      followMe: 'следить за мной',
      following: 'слежу за вами',
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
      panelOpen: 'the route panel is open',
      dragHint: 'drag · arrow keys',
      lineFromApp: 'Line drawn in this app',
      lineFromAgent: "Line comes from the agent's verified plan",
      lineMissing: 'The agent returned stops without a verified line — no line shown',
      followMe: 'follow me',
      following: 'following you',
    },
  },
} satisfies LocaleArea<{ map: Record<string, string> }>;
