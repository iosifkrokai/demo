/** One distance, in one place.
 *
 * Before this file the tree measured metres four times, with four *different*
 * formulae: two equirectangular approximations (`111_320` and `EARTH_RADIUS_M`),
 * a flat plane, and a great-circle. They disagreed, so "the same" stop could be
 * 39 m away for the guide and 40 m away for the arrival check.
 *
 * The one kept is the great-circle (haversine) distance. It is the only formula
 * here whose error does not grow with latitude, it was already what the map's
 * service thinning used, and at the guide's own scale both approximations sit
 * within a metre per kilometre of it — so the visible numbers barely move while
 * the definition stops being ambiguous.
 */

/** A point on the map. Callers pass our own shapes (`{ lat, lon }`), which is what every
 *  site already carried — the extra fields a `Fix` or a `Place` brings are simply ignored. */
export interface LatLon {
  lat: number;
  lon: number;
}

/** Mean Earth radius, in metres — the same constant every previous formula used. */
const EARTH_RADIUS_M = 6_371_000;

const toRad = (degrees: number): number => (degrees * Math.PI) / 180;

/** Great-circle distance between two points, in metres. */
export const metresBetween = (a: LatLon, b: LatLon): number => {
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(s)));
};
