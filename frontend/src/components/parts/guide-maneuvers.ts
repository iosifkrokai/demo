/**
 * What the navigator says next.
 *
 * A pedestrian route is not a car route: Valhalla returns a string of
 * three-metre steps («Поверните направо на пешеходную дорожку», then «Продолжайте
 * движение», then «Поверните налево…») where a car would get one turn. Measured
 * on a real Grodno walk (4 stops, 3.25 km): 50 maneuvers over 4 legs, and most
 * of them under 20 m apart.
 *
 * Announced one by one they say nothing useful — the banner flips to a new
 * instruction every few steps and the distance next to it is always «20 m» or
 * less, which reads as noise rather than guidance. A navigator announces the
 * turn the tourist is walking towards.
 *
 * Deliberately unit-free: the rule uses the distance BETWEEN maneuvers measured
 * on the route line the guide already builds, never Valhalla's own `length`,
 * whose unit follows the request's `units` and would quietly change meaning.
 */

/** Maneuvers closer together than this belong to the same move. */
export const MIN_TURN_GAP_M = 20;

/**
 * Collapse runs of micro-maneuvers into one.
 *
 * The last of a run is kept, not the first: walking a chain of steps, the
 * instruction that matters is the one that ends the chain and puts the tourist
 * on the street they are actually heading for. Order is preserved, so the
 * result is still «the next maneuver ahead», which is all the panel asks for.
 */
export const mergeMicroManeuvers = <T extends { along: number }>(
  list: T[]
): T[] => {
  const out: T[] = [];
  for (const maneuver of list) {
    const previous = out[out.length - 1];
    if (previous && maneuver.along - previous.along < MIN_TURN_GAP_M) {
      out[out.length - 1] = maneuver;
    } else {
      out.push(maneuver);
    }
  }
  return out;
};
