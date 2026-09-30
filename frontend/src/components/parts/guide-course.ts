/**
 * Which way the route goes — the course the camera should be looking along.
 *
 * The map used to be turned by the device's own heading (`coords.heading`), and
 * on a walk that is the wrong number twice over: a browser reports it only while
 * the tourist is moving, it is the direction the *phone* points rather than the
 * direction the *route* goes, and a hand-held phone wobbles. Measured effect: the
 * map turns away from the path and the tourist walks with the route running
 * sideways across the screen.
 *
 * The route itself always knows the answer: it is the direction from the point
 * where the tourist is to the point a little further along the line.
 */

/** How far ahead to look. 25 m is roughly five seconds of walking — far enough
 * to ignore the noise of a single shape point, near enough to still be «here». */
export const COURSE_LOOKAHEAD_M = 25;

export interface CourseLine {
  points: { lat: number; lon: number }[];
  /** Cumulative distance in metres at each point (`line.cum[i]`). */
  cum: number[];
}

/** Where the line is at a given distance along it, by linear interpolation. */
const pointAt = (
  line: CourseLine,
  alongM: number
): { lat: number; lon: number } | null => {
  const { points, cum } = line;
  if (points.length < 2 || cum.length < points.length) return null;
  const clamped = Math.min(Math.max(alongM, 0), cum[cum.length - 1] ?? 0);

  for (let i = 1; i < points.length; i++) {
    const from = cum[i - 1] ?? 0;
    const to = cum[i] ?? from;
    if (clamped > to) continue;
    const span = to - from;
    const ratio = span > 0 ? (clamped - from) / span : 0;
    const a = points[i - 1];
    const b = points[i];
    if (!a || !b) return null;
    return {
      lat: a.lat + (b.lat - a.lat) * ratio,
      lon: a.lon + (b.lon - a.lon) * ratio,
    };
  }
  return points[points.length - 1] ?? null;
};

/**
 * Bearing along the route at `alongM`, in degrees clockwise from north, or null
 * when there is no line to read a course from (a route without usable geometry:
 * the map then keeps whatever heading it has).
 */
export const courseAlongLine = (
  line: CourseLine | null,
  alongM: number,
  lookaheadM: number = COURSE_LOOKAHEAD_M
): number | null => {
  if (!line) return null;
  const total = line.cum[line.cum.length - 1] ?? 0;
  const along = Math.min(Math.max(alongM, 0), total);
  return courseBetween(line, along, along + lookaheadM, lookaheadM);
};

/**
 * The same course, but found from a position instead of a distance along the
 * line.
 *
 * Needed because «how far along the route am I» only advances on a trusted fix:
 * before the first good fix — or while the guide is not yet in its walking state
 * — there is no progress to read a course from, yet the map still has to know
 * which way the route goes the moment the tourist asks for «по курсу». The
 * nearest point of the line answers that.
 */
export const courseAtPoint = (
  line: CourseLine | null,
  lat: number,
  lon: number,
  lookaheadM: number = COURSE_LOOKAHEAD_M
): number | null => {
  if (!line || line.points.length < 2) return null;

  let nearest = 0;
  let best = Number.POSITIVE_INFINITY;
  for (let i = 1; i < line.points.length; i++) {
    const a = line.points[i - 1];
    const b = line.points[i];
    if (!a || !b) continue;
    const midLat = (a.lat + b.lat) / 2;
    const scale = Math.cos((midLat * Math.PI) / 180);
    const dx = (lon - a.lon) * scale;
    const dy = lat - a.lat;
    const ex = (b.lon - a.lon) * scale;
    const ey = b.lat - a.lat;
    const lengthSq = ex * ex + ey * ey;
    const t =
      lengthSq > 0
        ? Math.min(Math.max((dx * ex + dy * ey) / lengthSq, 0), 1)
        : 0;
    const px = dx - ex * t;
    const py = dy - ey * t;
    const distance = px * px + py * py;
    if (distance < best) {
      best = distance;
      const from = line.cum[i - 1] ?? 0;
      const to = line.cum[i] ?? from;
      nearest = from + (to - from) * t;
    }
  }

  return courseBetween(line, nearest, nearest + lookaheadM, lookaheadM);
};

/** Bearing between two distances along the line, with the end-of-line rule. */
const courseBetween = (
  line: CourseLine,
  fromM: number,
  toM: number,
  lookaheadM: number
): number | null => {
  const total = line.cum[line.cum.length - 1] ?? 0;
  const start = Math.min(Math.max(fromM, 0), total);
  const end = Math.min(Math.max(toM, 0), total);

  const here = pointAt(line, start);
  const ahead = pointAt(line, end);

  // Normally the course is «from here to a little further on». At the very end
  // of the line there is nothing further on, so the last segment is read
  // backwards — still pointing the way the tourist is walking, so the map does
  // not swing round at the last step of the route.
  let from = here;
  let to = ahead;
  if (from && to && from.lat === to.lat && from.lon === to.lon) {
    from = pointAt(line, start - lookaheadM);
    to = here;
  }
  if (!from || !to) return null;
  if (from.lat === to.lat && from.lon === to.lon) return null;

  const lat1 = (from.lat * Math.PI) / 180;
  const lat2 = (to.lat * Math.PI) / 180;
  const dLon = ((to.lon - from.lon) * Math.PI) / 180;

  const y = Math.sin(dLon) * Math.cos(lat2);
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  const bearing = (Math.atan2(y, x) * 180) / Math.PI;
  return (bearing + 360) % 360;
};
