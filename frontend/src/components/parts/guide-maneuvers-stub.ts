/**
 * TEMPORARY STUB — temporary stand-in while guide-maneuvers.ts is being rebuilt.
 *
 * A pedestrian route is a string of three-metre steps where a car would get one
 * turn. Announced one by one they read as noise, so runs of micro-maneuvers
 * collapse into the turn that actually ends the chain.
 *
 * The behaviour here is deliberately the simplest rule that matches the old
 * implementation: consecutive manoeuvres less than MIN_TURN_GAP_M apart are one
 * move, and the LAST of the run is the one kept. The original file is
 * recoverable from git history (`git show HEAD:frontend/src/components/parts/guide-maneuvers.ts`)
 * if the richer version is needed back.
 *
 * Unit-free: `along` is metres along the route line the guide already builds,
 * never Valhalla's own `length` (whose unit follows the request's `units`).
 */

/** Manoeuvres closer together than this belong to the same move. */
const MIN_TURN_GAP_M = 20;

/**
 * Collapse runs of micro-maneuuvres into one, keeping the last of each run.
 *
 * Order is preserved, so the result is still «the next manoeuvre ahead».
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
