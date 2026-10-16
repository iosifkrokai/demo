/** Locale-aware formatting for the two interface languages. */

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

/** A number with the locale's separators: a comma in Russian, a dot in English. */
export const formatNumber = (
  value: number,
  options?: Intl.NumberFormatOptions
) => value.toLocaleString(locale(), options);

/** Grodno is UTC+3 all year; the server's `local_at` shifts an instant by exactly this. */
export const GRODNO_UTC_OFFSET_MS = 3 * 60 * 60 * 1000;

/** The instant the server judged against, as the wall clock on the tourist's own clock. */
export const formatMoment = (at: string | number | Date) =>
  new Intl.DateTimeFormat(locale(), {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  }).format(new Date(new Date(at).getTime() + GRODNO_UTC_OFFSET_MS));
