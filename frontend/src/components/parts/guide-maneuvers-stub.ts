/** TEMPORARY STUB — temporary stand-in while guide-maneuvers.ts is being rebuilt. */

/** Manoeuvres closer together than this belong to the same move. */
const MIN_TURN_GAP_M = 20;

/** Collapse runs of micro-maneuuvres into one, keeping the last of each run. */
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
