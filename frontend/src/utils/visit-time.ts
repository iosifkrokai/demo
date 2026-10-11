/** How long the tourist wants to spend at a stop. */
export const VISIT_MIN = 5;
export const VISIT_MAX = 480;
export const VISIT_STEP = 10;

/** placeId → minutes the tourist chose. */
export type VisitOverrides = Record<string, number>;

interface StoredVisitTimes {
  route: string;
  overrides: VisitOverrides;
}

const STORAGE_KEY = 'grodno-guide-visit-minutes';

export const clampVisit = (minutes: number): number =>
  Math.max(VISIT_MIN, Math.min(VISIT_MAX, Math.round(minutes)));

export const loadVisitOverrides = (routeKey: string): VisitOverrides => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as StoredVisitTimes;
    if (parsed.route !== routeKey || !parsed.overrides) return {};
    return Object.fromEntries(
      Object.entries(parsed.overrides).filter(
        ([, minutes]) => typeof minutes === 'number' && Number.isFinite(minutes)
      )
    );
  } catch {
    return {};
  }
};

export const saveVisitOverrides = (
  routeKey: string,
  overrides: VisitOverrides
): void => {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ route: routeKey, overrides } satisfies StoredVisitTimes)
    );
  } catch {}
};

/** The minutes to plan with for one stop: the tourist's own, else the estimate. */
export const visitMinutesFor = (
  id: string,
  estimate: number | null | undefined,
  overrides: VisitOverrides
): number | null => {
  const own = overrides[id];
  if (typeof own === 'number' && Number.isFinite(own)) return own;
  return estimate ?? null;
};
