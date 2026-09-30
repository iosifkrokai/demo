/**
 * The panel's own controls: the handle on its edge, the grab bar of the mobile
 * sheet and the way out of it.
 *
 * A separate area rather than a handful of keys in `sidebar.ts` because these
 * strings belong to the panel as a *thing*, not to what it shows: the same three
 * controls serve planning, itineraries and history.
 */
export interface LocaleArea<T = Record<string, unknown>> {
  ru: T;
  en: T;
}

export const panelArea = {
  ru: {
    panel: {
      expand: 'развернуть панель',
      collapse: 'свернуть панель',
      dragHint: 'потянуть, чтобы развернуть',
    },
  },
  en: {
    panel: {
      expand: 'expand the panel',
      collapse: 'collapse the panel',
      dragHint: 'drag to expand',
    },
  },
} satisfies LocaleArea;
