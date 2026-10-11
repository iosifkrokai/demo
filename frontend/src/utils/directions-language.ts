/** The language Valhalla writes turn-by-turn instructions in. */

import { isEnglish } from '@/i18n/format';

/** The instruction language follows the interface: ru by default, en for English. */
export type DirectionsLanguage = 'ru-RU' | 'en-US';

export function getDirectionsLanguage(): DirectionsLanguage {
  return isEnglish() ? 'en-US' : 'ru-RU';
}
