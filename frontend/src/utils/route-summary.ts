/** The numbers under the route heading, from whichever answer produced the route:
 * a generated route and a re-measured one must land on the panel the same way. */

import type { AgentRouteResponse } from '@/components/types';
import type { RoutePanelSummary } from '@/stores/directions-store';

/** Minutes a stop is assumed to take when nobody has said. */
const DEFAULT_VISIT_MINUTES = 15;

export const summaryOf = (answer: AgentRouteResponse): RoutePanelSummary => {
  const points = answer.points ?? [];
  const travelMinutes = Math.round((answer.summary?.time_seconds ?? 0) / 60);
  return {
    stops: points.length,
    km: answer.summary?.length_km ?? null,
    travelMinutes,
    walkMinutes: answer.budget?.walk_minutes ?? travelMinutes,
    visitMinutes:
      answer.budget?.visit_minutes ??
      points.reduce(
        (total, point) =>
          total + (point.visit_minutes ?? DEFAULT_VISIT_MINUTES),
        0
      ),
    budgetMinutes: answer.budget?.budget_minutes ?? null,
    fits: answer.budget?.fits ?? true,
  };
};
