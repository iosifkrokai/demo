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
    },
  },
} satisfies LocaleArea<{ map: Record<string, string> }>;
