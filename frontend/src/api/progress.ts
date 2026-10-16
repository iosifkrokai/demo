/** Where the route request got to (`GET /routes/progress/{id}`). */

import { request } from './request';

/** The stages the pipeline reports, in the order it reaches them. */
export type RouteStageCode =
  | 'interpreting_request'
  | 'searching_places'
  | 'selecting_candidates'
  | 'measuring_legs'
  | 'ordering_stops'
  | 'repairing_route'
  | 'drawing_line'
  | 'checking_requirements'
  | 'done';

export interface RouteProgress {
  stage: RouteStageCode;
  done: boolean;
  failed: boolean;
  elapsedMs: number;
}

export const newProgressId = (): string =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

const STAGE_CODES: ReadonlySet<string> = new Set<RouteStageCode>([
  'interpreting_request',
  'searching_places',
  'selecting_candidates',
  'measuring_legs',
  'ordering_stops',
  'repairing_route',
  'drawing_line',
  'checking_requirements',
  'done',
]);

/** Unknown codes are dropped rather than shown: the client never invents a stage. */
export const asStageCode = (value: unknown): RouteStageCode | null =>
  typeof value === 'string' && STAGE_CODES.has(value)
    ? (value as RouteStageCode)
    : null;

/** The route's current stage; `null` when the server cannot say (unknown or unreachable). */
export const fetchRouteProgress = async (
  progressId: string,
  signal: AbortSignal
): Promise<RouteProgress | null> => {
  let body: Record<string, unknown>;
  try {
    body = await request<Record<string, unknown>>(
      `/routes/progress/${encodeURIComponent(progressId)}`,
      { signal }
    );
  } catch {
    // Unreachable, 404 or a non-JSON answer all read the same to the panel: keep polling.
    return null;
  }

  const stage = asStageCode(body.stage);
  if (!stage) return null;

  return {
    stage,
    done: body.done === true,
    failed: body.failed === true,
    elapsedMs: typeof body.elapsed_ms === 'number' ? body.elapsed_ms : 0,
  };
};
