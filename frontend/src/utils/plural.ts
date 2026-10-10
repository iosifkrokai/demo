/** Russian counting and number formatting, in one place. */

/** The three Russian forms of a counted noun: one / few / many. */
export type PluralForms = readonly [string, string, string];

/** Pick the right form of a counted noun. */
export const pluralRu = (count: number, forms: PluralForms): string => {
  const abs = Math.abs(Math.trunc(count));
  const mod100 = abs % 100;
  if (mod100 >= 11 && mod100 <= 14) return forms[2];
  const mod10 = abs % 10;
  if (mod10 === 1) return forms[0];
  if (mod10 >= 2 && mod10 <= 4) return forms[1];
  return forms[2];
};

/** «3 точки», «1 точка», «5 точек» — a count with its noun, as one string. */
export const pluralCountRu = (count: number, forms: PluralForms): string =>
  `${count} ${pluralRu(count, forms)}`;

/** Forms for the things the guide counts. */
export const POINT_FORMS: PluralForms = ['точка', 'точки', 'точек'];
export const STOP_FORMS: PluralForms = ['остановка', 'остановки', 'остановок'];
export const MINUTE_FORMS: PluralForms = ['минута', 'минуты', 'минут'];
/** Places in a saved route («3 места», not «3 мест»). */
export const PLACE_FORMS: PluralForms = ['место', 'места', 'мест'];
/** Children in the party («1 ребёнок», «2 ребёнка», «5 детей»). */
export const CHILD_FORMS: PluralForms = ['ребёнок', 'ребёнка', 'детей'];

/** The noun alone: the label under a stat-tile number («3» / «точки»). */
export const pointsLabel = (count: number) => pluralRu(count, POINT_FORMS);
export const stopsLabel = (count: number) => pluralRu(count, STOP_FORMS);
export const minutesLabel = (count: number) => pluralRu(count, MINUTE_FORMS);

/** «3 места», «1 место», «5 мест» — places a saved route lists. */
export const placeCountRu = (count: number) =>
  pluralCountRu(count, PLACE_FORMS);

/** Russian decimal separator is a comma: `decimalRu(1.3)` → `«1,3»`. */
export const decimalRu = (value: number, digits = 1): string =>
  value.toFixed(digits).replace('.', ',');

/** «240 м», «1,3 км» — a distance the tourist walks. */
export const formatDistanceRu = (metres: number): string =>
  metres >= 1000 ? `${decimalRu(metres / 1000)} км` : `${Math.round(metres)} м`;
