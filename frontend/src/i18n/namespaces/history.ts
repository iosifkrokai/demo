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
