import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import { en } from './en';
import { guideArea } from './namespaces/guide';
import { historyArea } from './namespaces/history';
import { mapArea } from './namespaces/map';
import { ru } from './ru';

/**
 * Interface language.
 *
 * Deliberately separate from the *data* language: place names, blurbs and turn
 * instructions come from the backend in the language the request asks for
 * (`language` in the generate body), while these strings are the app's own
 * chrome. Choosing English here changes both — see `setLanguage`.
 */
export const LANGUAGES = ['ru', 'en'] as const;
export type Language = (typeof LANGUAGES)[number];

export const LANGUAGE_STORAGE_KEY = 'grodno-language';

const isLanguage = (value: unknown): value is Language =>
  value === 'ru' || value === 'en';

/**
 * What to start in: the tourist's own choice, else their browser.
 *
 * The browser is asked, not assumed: an English-speaking visitor should not
 * have to find the switch to read the panel at all.
 */
export const initialLanguage = (): Language => {
  try {
    const saved = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (isLanguage(saved)) return saved;
  } catch {
    // Private mode: no stored preference, which is not a reason to fail.
  }
  return typeof navigator !== 'undefined' &&
    navigator.language?.toLowerCase().startsWith('en')
    ? 'en'
    : 'ru';
};

/**
 * The flat dictionaries the app actually uses: the core area (`ru`/`en`) plus
 * every namespace area, merged by key. Exported so the parity test checks what
 * ships rather than a copy of it.
 */
export const flatRu = {
  ...ru,
  ...guideArea.ru,
  ...mapArea.ru,
  ...historyArea.ru,
};
export const flatEn = {
  ...en,
  ...guideArea.en,
  ...mapArea.en,
  ...historyArea.en,
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
  // Resources are bundled, so there is nothing to wait for: initialising
  // asynchronously would paint raw keys like `ask.placeholder` for one frame.
  initAsync: false,
});

/** Keep the document honest about what language it is in (screen readers, hyphenation). */
const applyDocumentLanguage = (lng: string) => {
  if (typeof document !== 'undefined') document.documentElement.lang = lng;
};

// Set on start too, not only on change: a reload with a stored choice must not
// leave the document claiming the language it was switched away from.
applyDocumentLanguage(i18n.language);
i18n.on('languageChanged', applyDocumentLanguage);

/** Remember the choice and switch the UI to it. */
export const setLanguage = (language: Language) => {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // Storing is a convenience; switching still works without it.
  }
  void i18n.changeLanguage(language);
};

export default i18n;
