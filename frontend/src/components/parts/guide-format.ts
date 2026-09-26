/**
 * Small shared helpers for the guide (проводник) and its parts: the walk
 * repeats them in the panel, in the next-stop card and in the stop list, so
 * one wording means one thing everywhere.
 *
 * Counting and number shape live in `@/utils/plural` — this module only says
 * how the guide phrases a duration, a distance and a failed request.
 */

import { formatDistanceRu, pluralCountRu, STOP_FORMS } from '@/utils/plural';

export { formatDistanceRu };

/**
 * «40 мин», «1 ч 10 мин», «2 ч» — how long a stop (or the rest) takes.
 * The abbreviated «мин» is deliberate: it sits inside sentences where the full
 * «минута/минуты/минут» would crowd the line. Use `minutesLabel` from
 * `@/utils/plural` when the word stands on its own.
 */
export const fmtMin = (min: number) => {
  if (min < 60) return `${min} мин`;
  const hours = Math.floor(min / 60);
  const rest = min % 60;
  return rest === 0 ? `${hours} ч` : `${hours} ч ${rest} мин`;
};

/**
 * «240 м», «1,3 км» — how far the tourist still has to walk.
 * Russian uses a comma as the decimal separator, never a dot.
 */
export const fmtDist = (metres: number) => formatDistanceRu(metres);

/** «3 остановки», «1 остановка», «5 остановок» — a count with its noun. */
export const fmtStops = (count: number) => pluralCountRu(count, STOP_FORMS);

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
  if (status === 404) {
    return 'агент не отвечает по этому адресу (404) — похоже, приложение обращается не к тому серверу. Это ошибка настройки, а не «ничего не найдено».';
  }
  if (status >= 500) {
    return `агент ответил ошибкой ${status} — попробуйте ещё раз`;
  }
  if (status === 401 || status === 403) {
    return `агент отклонил запрос (${status}) — проверьте доступ к сервису.`;
  }
  if (status === 422) {
    return 'агент не понял запрос (422) — переформулируйте, что хотите посмотреть.';
  }
  return `агент ответил ошибкой ${status} — попробуйте ещё раз`;
};

export const metresBetween = (
  a: { lat: number; lon: number },
  b: { lat: number; lon: number }
) => {
  const dLat = (a.lat - b.lat) * 111_320;
  const dLon = (a.lon - b.lon) * 111_320 * Math.cos((a.lat * Math.PI) / 180);
  return Math.hypot(dLat, dLon);
};
