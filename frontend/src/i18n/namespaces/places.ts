import type { LocaleArea } from './guide';

/** Keys of the full catalogue tab. */
export const placesArea = {
  ru: {
    places: {
      loading: 'Загружаю все точки…',
      loadFailed: 'Не удалось загрузить точки. Данные целы — не ответил агент.',
      retry: 'повторить',
      search: 'Название, город или категория',
      intro_one: '{{count}} точка в данных — весь каталог на карте и в списке.',
      intro_few: '{{count}} точки в данных — весь каталог на карте и в списке.',
      intro_many:
        '{{count}} точек в данных — весь каталог на карте и в списке.',
      noMatch: 'Ничего не нашлось — попробуйте другое слово.',
    },
  },
  en: {
    places: {
      loading: 'Loading all points…',
      loadFailed:
        'Could not load the points. The data is fine — the agent did not answer.',
      retry: 'retry',
      search: 'Name, town or category',
      intro_one:
        '{{count}} place in the dataset — the whole catalogue, on the map and as a list.',
      intro_other:
        '{{count}} places in the dataset — the whole catalogue, on the map and as a list.',
      noMatch: 'Nothing matched — try another word.',
    },
  },
} satisfies LocaleArea<{ places: Record<string, string> }>;
