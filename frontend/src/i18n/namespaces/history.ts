import type { LocaleArea } from './guide';

/** Keys of the history tab, server-saved routes and the client layer's errors. */
export const historyArea = {
  ru: {
    history: {
      savedTitle: 'сохранённые маршруты',
      untitled: 'маршрут без названия',
      save: 'сохранить маршрут',
      saving: 'сохраняю…',
      saved: 'маршрут сохранён на сервере',
      deleteMine: 'удалить мои данные',
      deleting: 'удаляю…',
      deleted: 'данные удалены — маршруты и предпочтения стёрты',
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
    errors: {
      noServer: 'агент недоступен — сохранение не работает',
      badJson: 'агент ответил не JSON',
    },
  },
  en: {
    history: {
      savedTitle: 'saved routes',
      untitled: 'untitled route',
      save: 'save the route',
      saving: 'saving…',
      saved: 'the route is saved on the server',
      deleteMine: 'delete my data',
      deleting: 'deleting…',
      deleted: 'data deleted — routes and preferences are gone',
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
    errors: {
      noServer: 'the agent is unreachable — saving does not work',
      badJson: 'the agent answered with something that is not JSON',
    },
  },
} satisfies LocaleArea<{
  history: Record<string, string>;
  errors: Record<string, string>;
}>;
