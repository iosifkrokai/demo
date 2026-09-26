/**
 * How long the tourist wants to spend at a stop.
 *
 * The dataset gives an estimate per place (`visitMinutes`, from the taxonomy) and
 * the UI shows it as approximate — «≈ 40 мин, столько обычно и оставляют». The
 * tourist is the one who decides, so their number is stored next to the route
 * (same idea as the walk progress) and every total that depends on visit time
 * is computed from it.
 */
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
    // unreadable storage — fall back to the dataset estimates
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
  } catch {
    // private mode / quota — the guide keeps working with estimates
  }
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
