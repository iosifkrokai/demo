import type { LocaleArea } from './guide';

/** Keys of the admin section: the dashboard header, the users and places panels. */
export const adminArea = {
  ru: {
    admin: {
      title: 'Админка',
      guestHint:
        'Раздел доступен только администраторам. Войдите под аккаунтом с правами админа.',
      forbiddenTitle: 'Нужны права администратора',
      forbiddenBody:
        'Вы вошли как {{email}}, но у этого аккаунта роль «пользователь».',
      backToMap: 'к карте',
      loading: 'загружаю…',
      retry: 'повторить',
      nothingFound: 'ничего не найдено',
      tabsLabel: 'Разделы админки',
      tabs: {
        users: 'Пользователи',
        places: 'Места',
      },
      stats: {
        users: 'пользователей',
        admins: 'админов',
        places: 'мест',
        visited: 'посещений',
        routes: 'маршрутов',
      },
      users: {
        search: 'поиск по почте или имени',
        total: 'всего: {{count}}',
        self: 'это вы',
        stats:
          'посещённых: {{visited}} · маршрутов: {{routes}} · вход: {{login}}',
        role: 'роль',
        roleUser: 'пользователь',
        roleAdmin: 'админ',
        delete: 'удалить',
        confirmDelete: 'Удалить аккаунт {{email}}?',
      },
      places: {
        search: 'поиск по названию или городу',
        categoryPlaceholder: 'категория',
        total: 'всего: {{count}}',
        newPlace: 'новое место',
        minutes: '{{count}} мин',
        edit: 'изменить',
        save: 'сохранить',
        cancel: 'отмена',
        create: 'создать',
        selected: 'выбрано: {{name}}',
        confirmDelete: 'Удалить место «{{name}}»?',
      },
      fields: {
        name: 'название',
        category: 'категория',
        town: 'город',
        visitMinutes: 'время осмотра, мин',
        lat: 'широта',
        lon: 'долгота',
        blurb: 'описание',
        funFact: 'любопытный факт',
        sourceUrl: 'ссылка-источник',
      },
    },
  },
  en: {
    admin: {
      title: 'Admin',
      guestHint:
        'This section is for administrators. Sign in with an admin account.',
      forbiddenTitle: 'Administrator rights required',
      forbiddenBody:
        'You are signed in as {{email}}, but this account has the "user" role.',
      backToMap: 'to the map',
      loading: 'loading…',
      retry: 'retry',
      nothingFound: 'nothing found',
      tabsLabel: 'Admin sections',
      tabs: {
        users: 'Users',
        places: 'Places',
      },
      stats: {
        users: 'users',
        admins: 'admins',
        places: 'places',
        visited: 'visits',
        routes: 'routes',
      },
      users: {
        search: 'search by email or name',
        total: 'total: {{count}}',
        self: 'this is you',
        stats: 'visited: {{visited}} · routes: {{routes}} · login: {{login}}',
        role: 'role',
        roleUser: 'user',
        roleAdmin: 'admin',
        delete: 'delete',
        confirmDelete: 'Delete the account {{email}}?',
      },
      places: {
        search: 'search by name or town',
        categoryPlaceholder: 'category',
        total: 'total: {{count}}',
        newPlace: 'new place',
        minutes: '{{count}} min',
        edit: 'edit',
        save: 'save',
        cancel: 'cancel',
        create: 'create',
        selected: 'selected: {{name}}',
        confirmDelete: 'Delete the place "{{name}}"?',
      },
      fields: {
        name: 'name',
        category: 'category',
        town: 'town',
        visitMinutes: 'visit time, min',
        lat: 'latitude',
        lon: 'longitude',
        blurb: 'description',
        funFact: 'fun fact',
        sourceUrl: 'source link',
      },
    },
  },
} satisfies LocaleArea<{
  admin: Record<string, unknown>;
}>;
