import type { LocaleArea } from './guide';

/** Keys of the local built-routes history tab. */
export const historyArea = {
  ru: {
    history: {
      built: 'Построенные маршруты',
      clear: 'очистить',
      clearTitle: 'очистить историю',
      empty:
        'Здесь появятся маршруты, которые вы построите — их можно будет открыть заново вместе с остановками.',
      walked: 'пройден',
      walkedPartial: 'пройдено {{visited}} из {{total}}',
      unlimited: 'без лимита',
      remove: 'удалить',
      removeTitle: 'удалить из истории',
      places_one: '{{count}} место',
      places_few: '{{count}} места',
      places_many: '{{count}} мест',
    },
  },
  en: {
    history: {
      built: 'Built routes',
      clear: 'clear',
      clearTitle: 'clear the history',
      empty:
        'Routes you build will show up here — you can open them again with their stops.',
      walked: 'walked',
      walkedPartial: '{{visited}} of {{total}} done',
      unlimited: 'no limit',
      remove: 'delete',
      removeTitle: 'remove from the history',
      places_one: '{{count}} place',
      places_other: '{{count}} places',
    },
  },
} satisfies LocaleArea<{ history: Record<string, string> }>;
