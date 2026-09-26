/**
 * Russian counting and number formatting, in one place.
 *
 * Russian counted nouns take three forms: 1 → «точка», 2–4 → «точки»,
 * 5+ and 11–14 → «точек». The rule is the same for every noun, so callers
 * pass their own three forms and no caller re-implements the rule.
 *
 * Why a shared module: the panel, the next-stop card and the map all print
 * the same counts and distances, and they drifted apart — «3 точек» next to
 * «1.3 км». One wording means one thing everywhere.
 */

/** The three Russian forms of a counted noun: one / few / many. */
export type PluralForms = readonly [string, string, string];

/**
 * Pick the right form of a counted noun.
 * `pluralRu(3, ['точка', 'точки', 'точек']) === 'точки'`.
 */
export const pluralRu = (count: number, forms: PluralForms): string => {
  const abs = Math.abs(Math.trunc(count));
  const mod100 = abs % 100;
  // 11–14 take the "many" form even though they end in 1–4.
  if (mod100 >= 11 && mod100 <= 14) return forms[2];
  const mod10 = abs % 10;
  if (mod10 === 1) return forms[0];
  if (mod10 >= 2 && mod10 <= 4) return forms[1];
  return forms[2];
};

/** «3 точки», «1 точка», «5 точек» — a count with its noun, as one string. */
export const pluralCountRu = (count: number, forms: PluralForms): string =>
  `${count} ${pluralRu(count, forms)}`;

/** Forms for the things the guide counts. Pass the type, not a bare string. */
export const POINT_FORMS: PluralForms = ['точка', 'точки', 'точек'];
export const STOP_FORMS: PluralForms = ['остановка', 'остановки', 'остановок'];
export const MINUTE_FORMS: PluralForms = ['минута', 'минуты', 'минут'];

/** The noun alone: the label under a stat-tile number («3» / «точки»). */
export const pointsLabel = (count: number) => pluralRu(count, POINT_FORMS);
export const stopsLabel = (count: number) => pluralRu(count, STOP_FORMS);
export const minutesLabel = (count: number) => pluralRu(count, MINUTE_FORMS);

/**
 * Russian decimal separator is a comma: `decimalRu(1.3)` → `«1,3»`.
 * `toFixed` is the one place a dot is still correct, so the swap happens here.
 */
export const decimalRu = (value: number, digits = 1): string =>
  value.toFixed(digits).replace('.', ',');

/**
 * «240 м», «1,3 км» — a distance the tourist walks. Under a kilometre it is
 * metres, rounded; a kilometre or more keeps one decimal and a comma.
 */
export const formatDistanceRu = (metres: number): string =>
  metres >= 1000 ? `${decimalRu(metres / 1000)} км` : `${Math.round(metres)} м`;
