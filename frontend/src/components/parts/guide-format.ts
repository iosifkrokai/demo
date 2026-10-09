/**
 * Small shared helpers for the guide and its parts: the walk
 * repeats them in the panel, in the next-stop card and in the stop list, so
 * one wording means one thing everywhere.
 *
 * The words themselves live in the `guide` area of the dictionary; these
 * helpers only say how the guide phrases a duration, a distance, a counted noun
 * and a failed request. They are plain functions, not components, so they reach
 * i18next through the shared instance — the alternative, threading `t` through
 * every caller, would drag the map and the history list into the guide's area.
 *
 * Counted nouns follow the project's two accepted ways: Russian goes through
 * `pluralRu`/`STOP_FORMS` from `@/utils/plural`, English through the
 * dictionary's own `_one`/`_other` pair.
 */

import i18n from '@/i18n';
import {
  decimalRu,
  formatDistanceRu,
  pluralCountRu,
  STOP_FORMS,
} from '@/utils/plural';

export { formatDistanceRu };

/** True while the interface is in English — the number shape differs too. */
const isEnglish = () => i18n.language?.toLowerCase().startsWith('en') ?? false;

/** The decimal separator: a comma in Russian, a dot in English. */
const decimal = (value: number, digits = 1) =>
  isEnglish() ? value.toFixed(digits) : decimalRu(value, digits);

/**
 * «40 мин», «1 ч 10 мин», «2 ч» — how long a stop (or the rest) takes.
 * The abbreviated unit is deliberate: it sits inside sentences where the full
 * «минута/минуты/минут» would crowd the line. Use `minutesLabel` from
 * `@/utils/plural` when the word stands on its own.
 */
export const fmtMin = (min: number) => {
  const mins = Math.max(0, Math.round(min));
  if (mins < 60) return i18n.t('guide.minutes', { count: mins });
  const hours = Math.floor(mins / 60);
  const rest = mins % 60;
  const hourPart = i18n.t('guide.hours', { count: hours });
  return rest
    ? `${hourPart} ${i18n.t('guide.minutes', { count: rest })}`
    : hourPart;
};

/**
 * «240 м», «1,3 км» — how far the tourist still has to walk.
 * Russian uses a comma as the decimal separator, English a dot.
 */
export const fmtDist = (metres: number) =>
  metres >= 1000
    ? i18n.t('guide.km', { value: decimal(metres / 1000) })
    : i18n.t('guide.metres', { value: String(Math.round(metres)) });

/** «3 остановки», «1 остановка», «5 остановок» — a count with its noun. */
export const fmtStops = (count: number) =>
  isEnglish()
    ? i18n.t('guide.stops', { count })
    : pluralCountRu(count, STOP_FORMS);

/**
 * The one honest wording for a failed `/routes/generate` request.
 *
 * The status alone is not an answer: a 404 is not "nothing found" — the agent
 * answers 200 with an empty plan when the base has no match. A 404 means this
 * app is pointed at something that is not the agent API (wrong address, route
 * not mounted), which is a connectivity/configuration problem. Dressing that
 * up as an empty result would be a lie about the data, so it gets its own
 * message. Everything else keeps the plain «ошибкой N» wording.
 */
export const agentErrorMessage = (status: number) => {
  if (status === 404) return i18n.t('guide.agentNotFound');
  if (status >= 500) return i18n.t('guide.agentServerError', { status });
  if (status === 401 || status === 403) {
    return i18n.t('guide.agentDenied', { status });
  }
  if (status === 422) return i18n.t('guide.agentBadRequest', { status });
  return i18n.t('guide.agentServerError', { status });
};

export const metresBetween = (
  a: { lat: number; lon: number },
  b: { lat: number; lon: number }
) => {
  const dLat = (a.lat - b.lat) * 111_320;
  const dLon = (a.lon - b.lon) * 111_320 * Math.cos((a.lat * Math.PI) / 180);
  return Math.hypot(dLat, dLon);
};
