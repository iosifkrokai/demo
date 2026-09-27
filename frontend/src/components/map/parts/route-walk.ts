/**
 * Cutting the drawn route at the tourist.
 *
 * A navigator does not draw the whole route behind you: the part already walked
 * fades and the line in front stays. This is the arithmetic for that — find
 * where the tourist sits along the line, and return the two halves — kept apart
 * from the map layer so it can be reasoned about (and tested) on its own.
 *
 * The line is a list of coordinates with no distances attached, so distances are
 * measured here. Projection onto a segment is planar: at the scale of a city
 * block (tens of metres, a few kilometres at most) the error is far below the
 * width of the stroke, and the alternative — metres of spherical trigonometry —
 * would buy nothing a tourist could see.
 */

export interface LonLat {
  lat: number;
  lon: number;
}

/** Coordinates as the map layer wants them: [lon, lat]. */
export type LineCoords = [number, number][];

const EARTH_RADIUS_M = 6_371_000;

/** Distance between two points in metres (equirectangular, good enough at city scale). */
export const metresBetween = (a: LonLat, b: LonLat): number => {
  const meanLat = ((a.lat + b.lat) / 2) * (Math.PI / 180);
  const dLat = (b.lat - a.lat) * (Math.PI / 180);
  const dLon = (b.lon - a.lon) * (Math.PI / 180);
  return (
    EARTH_RADIUS_M * Math.hypot(dLat, dLon * Math.cos(meanLat))
  );
};

export interface WalkSplit {
  /** The part already behind the tourist, ending exactly at their position. */
  walked: LineCoords;
  /** The part still ahead, starting exactly at their position. */
  remaining: LineCoords;
  /** Metres walked along the line. */
  travelled: number;
  /** Metres of line in total. */
  total: number;
}

/**
 * Where the tourist is along the line, and the two halves at that point.
 *
 * `null` when there is nothing to split: a degenerate line, or a position so far
 * from the route that the nearest point is meaningless — a walker in another
 * city would otherwise see the route cut at its own start and think they had
 * walked it.
 */
export const splitAtPosition = (
  coords: LineCoords,
  position: LonLat,
  maxDistanceM = 500
): WalkSplit | null => {
  if (coords.length < 2) return null;

  let total = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  let bestAlong = 0;
  let bestSegment = 0;
  let bestPoint: LineCoords[number] = coords[0]!;

  for (let i = 1; i < coords.length; i += 1) {
    const a = coords[i - 1]!;
    const b = coords[i]!;
    const aLonLat: LonLat = { lat: a[1], lon: a[0] };
    const bLonLat: LonLat = { lat: b[1], lon: b[0] };
    const segment = metresBetween(aLonLat, bLonLat);

    // Project onto the segment in degree space: the two axes are scaled the same
    // way here, so the parameter transfers to metres without further care.
    const dLon = b[0] - a[0];
    const dLat = b[1] - a[1];
    const span = dLon * dLon + dLat * dLat;
    let t = 0;
    if (span > 0) {
      t = ((position.lon - a[0]) * dLon + (position.lat - a[1]) * dLat) / span;
      t = Math.max(0, Math.min(1, t));
    }
    const point: LineCoords[number] = [a[0] + dLon * t, a[1] + dLat * t];
    const distance = metresBetween(position, { lat: point[1], lon: point[0] });

    if (distance < bestDistance) {
      bestDistance = distance;
      bestAlong = total + segment * t;
      bestSegment = i - 1;
      bestPoint = point;
    }
    total += segment;
  }

  if (bestDistance > maxDistanceM) return null;

  // Both halves meet exactly at the tourist. The position is added to a half
  // only when it is not already the vertex that half ends (or starts) on, so a
  // walker standing on a corner does not get that corner twice — which is what
  // drew a zero-length stub of line under the dot.
  const atVertex = (point: LineCoords[number], vertex: LineCoords[number]) =>
    metresBetween({ lat: point[1], lon: point[0] }, { lat: vertex[1], lon: vertex[0] }) <
    0.5;

  const walked: LineCoords = coords.slice(0, bestSegment + 1);
  if (!atVertex(bestPoint, coords[bestSegment]!)) walked.push(bestPoint);

  const ahead = coords.slice(bestSegment + 1);
  const remaining: LineCoords = atVertex(bestPoint, ahead[0]!)
    ? ahead
    : [bestPoint, ...ahead];

  return { walked, remaining, travelled: bestAlong, total };
};
