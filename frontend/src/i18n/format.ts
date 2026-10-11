/** Locale-aware formatting for the two interface languages. */

import type { TFunction } from 'i18next';

import i18n from '@/i18n';

/** True while the interface is in English — the number shape differs too. */
export const isEnglish = () =>
  i18n.language?.toLowerCase().startsWith('en') ?? false;

/** The BCP-47 locale that matches the interface language. */
export const locale = () => (isEnglish() ? 'en-GB' : 'ru-RU');

/** A date in the reader's language: «3 января 2026» / «3 January 2026». */
export const formatDate = (
  value: string | number | Date,
  options?: Intl.DateTimeFormatOptions
) =>
  new Date(value).toLocaleDateString(
    locale(),
    options ?? { day: 'numeric', month: 'long' }
  );

/** A date with the time, mediated by the same locale. */
export const formatDateTime = (value: string | number | Date) =>
  new Date(value).toLocaleString(locale(), {
    dateStyle: 'medium',
    timeStyle: 'short',
  });

/** A number with the locale's separators: a comma in Russian, a dot in English. */
export const formatNumber = (
  value: number,
  options?: Intl.NumberFormatOptions
) => value.toLocaleString(locale(), options);

/** A counted noun through i18next plurals: x_one/_few/_many (ru), x_one/_other (en). */
export const formatCount = (t: TFunction, key: string, count: number) =>
  t(key, { count });
