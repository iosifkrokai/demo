/**
 * Small shared helpers for the guide (проводник) and its parts: the walk
 * repeats them in the panel, in the next-stop card and in the stop list, so
 * one wording means one thing everywhere.
 */

/** «40 мин», «1 ч 10 мин», «2 ч» — how long a stop (or the rest) takes. */
export const fmtMin = (min: number) => {
  if (min < 60) return `${min} мин`;
  const hours = Math.floor(min / 60);
  const rest = min % 60;
  return rest === 0 ? `${hours} ч` : `${hours} ч ${rest} мин`;
};

/** «240 м», «1.2 км» — how far the tourist still has to walk. */
export const fmtDist = (metres: number) =>
  metres >= 1000
    ? `${(metres / 1000).toFixed(1)} км`
    : `${Math.round(metres)} м`;

export const metresBetween = (
  a: { lat: number; lon: number },
  b: { lat: number; lon: number }
) => {
  const dLat = (a.lat - b.lat) * 111_320;
  const dLon = (a.lon - b.lon) * 111_320 * Math.cos((a.lat * Math.PI) / 180);
  return Math.hypot(dLat, dLon);
};
