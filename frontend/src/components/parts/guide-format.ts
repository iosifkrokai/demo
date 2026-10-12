/** Shared helpers so the guide, next-stop card and stop list word things consistently. */

import i18n from '@/i18n';
import { formatNumber } from '@/i18n/format';

/** The decimal separator follows the interface: a comma in Russian, a dot in English. */
const decimal = (value: number, digits = 1) =>
  formatNumber(value, { maximumFractionDigits: digits, useGrouping: false });

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

/** «3 остановки», «3 stops» — a count with its noun, inflected by the dictionary. */
export const fmtStops = (count: number) => i18n.t('guide.stops', { count });

/** The one honest wording for a failed `/routes/generate` request. */
export const agentErrorMessage = (status: number, reason?: string | null) => {
  if (reason === 'llm_not_configured') return i18n.t('guide.agentNoReader');
  if (status === 404) return i18n.t('guide.agentNotFound');
  if (status >= 500) return i18n.t('guide.agentServerError', { status });
  if (status === 401 || status === 403) {
    return i18n.t('guide.agentDenied', { status });
  }
  if (status === 422) return i18n.t('guide.agentBadRequest', { status });
  return i18n.t('guide.agentServerError', { status });
};
