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
  /** Remember (or, with `null`, forget) how long the tourist wants to spend at one stop. */
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

/** Tourist's own visit times for one route, kept in `localStorage` beside walk progress. */
export const useVisitOverrides = (routeKey: string): VisitOverridesApi => {
  const [times, setTimes] = useState<VisitTimes>(() => ({
    key: routeKey,
    overrides: loadVisitOverrides(routeKey),
  }));

  if (times.key !== routeKey) {
    setTimes({ key: routeKey, overrides: loadVisitOverrides(routeKey) });
  }

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
