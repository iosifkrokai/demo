import type { LocaleArea } from './guide';

/** Keys of the transient toast messages raised from hooks and utilities. */
export const toastArea = {
  ru: {
    toast: {
      longRouteTitle: 'Длинный маршрут',
      longRouteBody: 'Показан автомобильный вариант: пешком такое не пройти.',
      noLineTitle: 'Нет проверенной линии',
      noLineBody:
        'Агент вернул остановки без геометрии — показываем точки без линии.',
      recalcFailedTitle: 'Маршрут не пересчитан',
      recalcFailedBody:
        'Не удалось измерить маршрут заново — на экране остаётся прежний.',
      errorTitle: 'Ошибка',
      noAddressesTitle: 'Адреса не найдены',
      noAddressesBody: 'По этому запросу ничего не нашлось.',
      warningTitle: 'Предупреждение (код {{code}})',
    },
  },
  en: {
    toast: {
      longRouteTitle: 'Long route',
      longRouteBody: 'Showing the car variant: this is too far to walk.',
      noLineTitle: 'No verified line',
      noLineBody:
        'The agent returned stops without geometry — showing the points without a line.',
      recalcFailedTitle: 'Route not re-measured',
      recalcFailedBody:
        'The route could not be measured again — the previous one stays on screen.',
      errorTitle: 'Error',
      noAddressesTitle: 'No addresses found',
      noAddressesBody: 'Nothing was found for that query.',
      warningTitle: 'Warning (code {{code}})',
    },
  },
} satisfies LocaleArea<{ toast: Record<string, string> }>;
