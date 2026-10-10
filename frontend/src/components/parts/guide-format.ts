/** Shared helpers so the guide, next-stop card and stop list word things consistently. */

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

/** «40 мин», «1 ч 10 мин», «2 ч» — how long a stop (or the rest) takes. */
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

/** «240 м», «1,3 км» — how far the tourist still has to walk. */
export const fmtDist = (metres: number) =>
  metres >= 1000
    ? i18n.t('guide.km', { value: decimal(metres / 1000) })
    : i18n.t('guide.metres', { value: String(Math.round(metres)) });

/** «3 остановки», «1 остановка», «5 остановок» — a count with its noun. */
export const fmtStops = (count: number) =>
  isEnglish()
    ? i18n.t('guide.stops', { count })
    : pluralCountRu(count, STOP_FORMS);

/** The one honest wording for a failed `/routes/generate` request. */
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
