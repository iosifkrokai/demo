/** The panel's controls: its edge handle, the mobile sheet's grab bar and close button. */
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
