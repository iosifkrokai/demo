import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import { en } from './en';
import { guideArea } from './namespaces/guide';
import { historyArea } from './namespaces/history';
import { mapArea } from './namespaces/map';
import { panelArea } from './namespaces/panel';
import { photoArea } from './namespaces/photo';
import { sidebarArea } from './namespaces/sidebar';
import { ru } from './ru';

/** Interface language. */
export const LANGUAGES = ['ru', 'en'] as const;
export type Language = (typeof LANGUAGES)[number];

export const LANGUAGE_STORAGE_KEY = 'grodno-language';

const isLanguage = (value: unknown): value is Language =>
  value === 'ru' || value === 'en';

/** What to start in: the tourist's own choice, else their browser. */
export const initialLanguage = (): Language => {
  try {
    const saved = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (isLanguage(saved)) return saved;
  } catch {}
  return typeof navigator !== 'undefined' &&
    navigator.language?.toLowerCase().startsWith('en')
    ? 'en'
    : 'ru';
};

/** Flat dictionaries the app uses: core `ru`/`en` merged with every namespace area. */
export const flatRu = {
  ...ru,
  ...guideArea.ru,
  ...mapArea.ru,
  ...historyArea.ru,
  ...sidebarArea.ru,
  ...photoArea.ru,
  ...panelArea.ru,
};
export const flatEn = {
  ...en,
  ...guideArea.en,
  ...mapArea.en,
  ...historyArea.en,
  ...sidebarArea.en,
  ...photoArea.en,
  ...panelArea.en,
};

void i18n.use(initReactI18next).init({
  resources: {
    ru: { translation: flatRu },
    en: { translation: flatEn },
  },
  lng: initialLanguage(),
  fallbackLng: 'ru',
  supportedLngs: [...LANGUAGES],
  interpolation: { escapeValue: false },
  returnNull: false,
  initAsync: false,
});

/** Keep the document honest about what language it is in (screen readers, hyphenation). */
const applyDocumentLanguage = (lng: string) => {
  if (typeof document !== 'undefined') document.documentElement.lang = lng;
};

applyDocumentLanguage(i18n.language);
i18n.on('languageChanged', applyDocumentLanguage);

/** Remember the choice and switch the UI to it. */
export const setLanguage = (language: Language) => {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {}
  void i18n.changeLanguage(language);
};

export default i18n;
