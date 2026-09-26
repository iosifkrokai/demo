import { useCallback, useEffect, useRef, useState } from 'react';

import {
  clampVisit,
  loadVisitOverrides,
  saveVisitOverrides,
  visitMinutesFor,
  type VisitOverrides,
} from '@/utils/visit-time';

export interface VisitOverridesApi {
  /** placeId → the tourist's own minutes, as it stands right now. */
  overrides: VisitOverrides;
  /**
   * Remember (or, with `null`, forget) how long the tourist wants to spend at
   * one stop. Saving here is what makes the number survive a reload.
   */
  setVisitMinutes: (id: string, minutes: number | null) => void;
  /** The minutes to plan with: the tourist's own number, else the estimate. */
  effectiveMinutesFor: (
    id: string,
    estimate: number | null | undefined
  ) => number | null;
}

/** The overrides together with the route they belong to. */
interface VisitTimes {
  key: string;
  overrides: VisitOverrides;
}

/**
 * The tourist's own visit times for one route, kept in `localStorage` next to
 * the walk progress (see `utils/visit-time`).
 *
 * `routeKey` identifies the route: a rebuilt route swaps in *its* saved times
 * rather than the previous route's. Storage reads and writes stay out of the
 * render pass — the map is loaded in the `useState` initializer, a new key is
 * adopted with React's render-phase state adjustment, and a change is written
 * in the event handler that made it.
 */
export const useVisitOverrides = (routeKey: string): VisitOverridesApi => {
  const [times, setTimes] = useState<VisitTimes>(() => ({
    key: routeKey,
    overrides: loadVisitOverrides(routeKey),
  }));

  // A rebuilt route is a new walk: adopt its saved times. React's own answer to
  // a changed prop — no effect, so no cascading render.
  if (times.key !== routeKey) {
    setTimes({ key: routeKey, overrides: loadVisitOverrides(routeKey) });
  }

  // The committed values, so a change can persist itself from the handler.
  const latest = useRef(times);
  useEffect(() => {
    latest.current = times;
  }, [times]);

  const setVisitMinutes = useCallback((id: string, minutes: number | null) => {
    const next = { ...latest.current.overrides };
    if (minutes == null) {
      delete next[id];
    } else {
      next[id] = clampVisit(minutes);
    }
    const updated: VisitTimes = { key: latest.current.key, overrides: next };
    latest.current = updated;
    saveVisitOverrides(updated.key, updated.overrides);
    setTimes(updated);
  }, []);

  const effectiveMinutesFor = useCallback(
    (id: string, estimate: number | null | undefined) =>
      visitMinutesFor(id, estimate, times.overrides),
    [times.overrides]
  );

  return {
    overrides: times.overrides,
    setVisitMinutes,
    effectiveMinutesFor,
  };
};
